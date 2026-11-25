# Adapted from Inworld Voice Isolator, Copyright 2026 Inworld AI (Apache-2.0).
# Pairvoice adaptation: offline v1 inference in MLX. See licenses/inworld/.
"""Inworld Voice Isolator v1。48 kHz モノラルの録音から前景の声を取り出す。"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import lru_cache
from itertools import groupby
from typing import cast

import mlx.core as mx
import mlx.nn as nn
import numpy as np
from scipy.fft import irfft, rfft

RATE = 48000
FFT = 2048
HOP = 480
CHUNK = 8 * RATE
OVERLAP = 2 * RATE
HEADS = 8
HEAD_DIM = 64
WIDTH = HEADS * HEAD_DIM
STATE = 16
DT_RANK = WIDTH // 16
CONV = 4
WINDOW = np.hanning(FFT + 1)[:-1].astype(np.float32)
BAND_PLAN = ((100, 1000), (250, 4000), (500, 8000), (1000, 16000), (2000, 24000))
BLOCKS = ("transformer", "mamba") * 3 + ("transformer",)


def _band_edges():
    edges = []
    start = 0
    for width, limit in BAND_PLAN:
        stop = round(limit * FFT / RATE)
        step = round(width * FFT / RATE)
        while start < stop:
            end = min(start + step, stop)
            edges.append((start, end))
            start = end
    return [*edges, (start, FFT // 2 + 1)]


EDGES = tuple(_band_edges())


# 1スレッドが1チャンネルの16要素の状態を持ち、時間方向に走査する。
_SCAN_SOURCE = """
    uint channel = thread_position_in_grid.x;
    uint row = thread_position_in_grid.y;
    uint frames = u_shape[1], channels = u_shape[2];
    float state[STATE] = {};
    for (uint t = 0; t < frames; ++t) {
        uint index = (row * frames + t) * channels + channel;
        uint base = (row * frames + t) * STATE;
        float step = delta[index], drive = step * u[index], sum = 0;
        for (uint n = 0; n < STATE; ++n) {
            state[n] = precise::exp(step * rate[channel * STATE + n]) * state[n]
                     + drive * b[base + n];
            sum += state[n] * c[base + n];
        }
        y[index] = sum + skip[channel] * u[index];
    }
"""


class _Layers:
    """チェックポイントの名前と演算をロード時に対応づける。"""

    def __init__(self, weights):
        self.weights = weights

    def linear(self, name, *, multihead=False):
        sep = "_" if multihead else "."
        weight = mx.contiguous(self.weights[name + sep + "weight"].T)
        bias = self.weights.get(name + sep + "bias")
        if bias is None:
            return lambda x: x @ weight
        return lambda x: x @ weight + bias

    def norm(self, name):
        weight = self.weights[name + ".weight"]
        bias = self.weights[name + ".bias"]
        return lambda x: mx.fast.layer_norm(x, weight, bias, 1e-5)


def _qkv(packed):
    batch, length, _ = packed.shape
    return packed.reshape(batch, length, 3, HEADS, HEAD_DIM).transpose(2, 0, 3, 1, 4)


def _attention_tail(layers, name, output, first, second):
    project = layers.linear(name + output)
    norm = layers.norm(name + ".norm2")
    ff1, ff2 = layers.linear(name + first), layers.linear(name + second)

    def finish(x, h):
        x = x + project(h.transpose(0, 2, 1, 3).reshape(x.shape))
        return x + ff2(nn.gelu(ff1(norm(x))))

    return finish


def _time_attention(layers, name):
    norm = layers.norm(name + ".norm1")
    project = layers.linear(name + ".attn.qkv")
    finish = _attention_tail(layers, name, ".attn.proj", ".ffn.0", ".ffn.3")

    def attend(x):
        q, k, v = _qkv(project(norm(x)))
        q = mx.fast.rope(q, HEAD_DIM, traditional=False, base=10000, scale=1, offset=0)
        k = mx.fast.rope(k, HEAD_DIM, traditional=False, base=10000, scale=1, offset=0)
        h = mx.fast.scaled_dot_product_attention(q, k, v, scale=HEAD_DIM**-0.5)
        projection = (h * v).sum(-1, keepdims=True) / mx.maximum(
            (v * v).sum(-1, keepdims=True), 1e-6
        )
        return finish(x, h - projection * v)

    return attend


def _band_attention(layers, name):
    norm = layers.norm(name + ".norm1")
    project = layers.linear(name + ".self_attn.in_proj", multihead=True)
    finish = _attention_tail(layers, name, ".self_attn.out_proj", ".linear1", ".linear2")

    def attend(x):
        q, k, v = _qkv(project(norm(x)))
        return finish(x, mx.fast.scaled_dot_product_attention(q, k, v, scale=HEAD_DIM**-0.5))

    return attend


def _mamba(layers, name, scan):
    project = layers.linear(name + ".in_proj")
    kernel = mx.contiguous(layers.weights[name + ".conv1d.weight"].transpose(0, 2, 1))
    bias = layers.weights[name + ".conv1d.bias"]
    select = layers.linear(name + ".x_proj")
    step = layers.linear(name + ".dt_proj")
    rate = -mx.exp(layers.weights[name + ".A_log"])
    skip = layers.weights[name + ".D"]
    output = layers.linear(name + ".out_proj")

    def forward(x):
        u, gate = mx.split(project(x), 2, axis=-1)
        padded = mx.pad(u, [(0, 0), (CONV - 1, 0), (0, 0)])
        u = nn.silu(mx.conv1d(padded, kernel, groups=u.shape[-1]) + bias)
        dt, b, c = mx.split(select(u), [DT_RANK, DT_RANK + STATE], axis=-1)
        delta = nn.softplus(step(dt))
        (y,) = scan(
            inputs=[
                u,
                delta,
                rate,
                b,
                c,
                skip,
            ],
            template=[("STATE", STATE)],
            grid=(u.shape[2], u.shape[0], 1),
            threadgroup=(128, 1, 1),
            output_shapes=[u.shape],
            output_dtypes=[mx.float32],
        )
        return output(y * nn.silu(gate))

    return forward


def _bidirectional(layers, name, scan):
    forward = _mamba(layers, name + ".forward_blocks", scan)
    backward = _mamba(layers, name + ".backward_blocks", scan)
    output = layers.linear(name + ".output_proj")
    norm = layers.norm(name + ".norm")

    def apply(x):
        both = mx.concatenate([forward(x) + x, backward(x[:, ::-1])[:, ::-1] + x], axis=-1)
        return x + norm(output(both))

    return apply


def _block(time, band):
    @mx.compile
    def apply(x):
        return band(time(x).swapaxes(0, 1)).swapaxes(0, 1)

    return apply


class _BandGroup:
    """同じ幅の連続する帯域を先頭の軸に置き、帯域別の行列積をまとめる。"""

    def __init__(self, layers, indices):
        self.first, self.stop = indices[0], indices[-1] + 1
        self.start, self.end = EDGES[self.first][0], EDGES[self.stop - 1][1]
        self.count = len(indices)

        def stack(pattern):
            return mx.stack([layers.weights[pattern.format(i)] for i in indices])

        def linear(prefix):
            weight = mx.contiguous(stack(prefix + ".weight").swapaxes(1, 2))
            bias = stack(prefix + ".bias")[:, None]
            return lambda x: x @ weight + bias

        def norm(prefix):
            weight = stack(prefix + ".weight")[:, None]
            bias = stack(prefix + ".bias")[:, None]
            return lambda x: mx.fast.layer_norm(x, None, None, 1e-5) * weight + bias

        self.input_norm = norm("encoders.{}.0")
        self.input_linear = linear("encoders.{}.1")
        self.output_norm = norm("denoise_decoder.heads.{}.0")
        self.hidden = linear("denoise_decoder.heads.{}.1")
        self.output = linear("denoise_decoder.heads.{}.3")

    def encode(self, components):
        frames = components.shape[0]
        x = components[:, 2 * self.start : 2 * self.end].reshape(frames, self.count, -1)
        return self.input_linear(self.input_norm(x.swapaxes(0, 1)))

    def decode(self, x):
        bands = x[self.first : self.stop]
        raw = self.output(nn.gelu(self.hidden(self.output_norm(bands))))
        return raw.swapaxes(0, 1).reshape(x.shape[1], self.end - self.start, 4)


def _overlap_add(windows):
    # HOP 幅の列に分けると、同じ列に寄与するのは最大5枚。各加算が全フレームを扱う。
    frames = windows.shape[0]
    lanes = (FFT + HOP - 1) // HOP
    padded = np.pad(windows, ((lanes - 1, lanes - 1), (0, lanes * HOP - FFT)))
    blocks = padded.reshape(-1, lanes, HOP)
    # 出力位置に重なる窓を古い順に並べる。古い窓ほど後ろの列が寄与する。
    neighbors = np.lib.stride_tricks.sliding_window_view(blocks[:, ::-1], lanes, axis=0)
    contributions = neighbors.diagonal(axis1=1, axis2=-1)
    return contributions.sum(axis=-1).reshape((frames + lanes - 1) * HOP)


@lru_cache(maxsize=8)
def _window_weight(frames):
    return _overlap_add(np.broadcast_to(WINDOW**2, (frames, FFT)))


class VoiceIsolator:
    def __init__(self, path: str):
        loaded = mx.load(path, return_metadata=True)
        if not isinstance(loaded, tuple):
            raise ValueError("Voice Isolator requires safetensors with config metadata")
        weights, metadata = loaded
        config = json.loads(metadata["config"])
        expected = {
            "width": WIDTH,
            "sample_rate": RATE,
            "n_fft": FFT,
            "hop_length": HOP,
            "band_plan": [list(pair) for pair in BAND_PLAN],
            "block_types": list(BLOCKS),
            "num_heads": HEADS,
            "mamba_state": STATE,
            "mamba_conv": CONV,
            "mamba_expand": 2,
            "chunk_seconds": CHUNK / RATE,
            "overlap_seconds": OVERLAP / RATE,
            "correction_bound": 0.5,
            "exclusive_axes": "time",
            "exclusive_self_attention": True,
            "causal": False,
            "dual_mode": False,
            "mode_film": False,
            "lookahead_frames_choices": [],
        }
        if any(config.get(k) != v for k, v in expected.items()):
            raise ValueError("Unsupported Voice Isolator architecture (expected offline v1)")
        layers = _Layers({name: value.astype(mx.float32) for name, value in weights.items()})
        scan = cast(
            Callable,
            mx.fast.metal_kernel(
                name="pairvoice_isolator_scan",
                input_names=["u", "delta", "rate", "b", "c", "skip"],
                output_names=["y"],
                source=_SCAN_SOURCE,
            ),
        )
        self.blocks = []
        for i, kind in enumerate(BLOCKS):
            name = f"blocks.{i}"
            if kind == "transformer":
                time = _time_attention(layers, name + ".time_transformer")
                band = _band_attention(layers, name + ".band_transformer")
            else:
                time = _bidirectional(layers, name + ".time_mamba", scan)
                band = _bidirectional(layers, name + ".freq_mamba", scan)
            self.blocks.append(_block(time, band))

        groups = [
            _BandGroup(layers, list(indices))
            for _, indices in groupby(range(len(EDGES)), key=lambda i: EDGES[i][1] - EDGES[i][0])
        ]
        embedding = layers.weights["band_embedding"][:, None]
        condition = layers.linear("denoise_decoder.condition")(mx.array([1.0, 0.0]))
        scale, shift = mx.split(condition, 2)

        @mx.compile
        def encode(components):
            return mx.concatenate([g.encode(components) for g in groups], axis=0) + embedding

        @mx.compile
        def decode(x, spectrum):
            x = x * (1 + scale) + shift
            raw = mx.tanh(mx.concatenate([g.decode(x) for g in groups], axis=1))
            mask = 2 * (raw[..., 0] + 1j * raw[..., 1])
            residual = raw[..., 2] + 1j * raw[..., 3]
            return spectrum * mask + residual / mx.maximum(1, mx.abs(residual)) * (
                0.5 * mx.abs(spectrum)
            )

        self.encode = encode
        self.decode = decode

    def _correction(self, spectrum):
        # complex64 の実部・虚部は交互に並ぶ。同じ転送からエンコーダの入力も得る。
        device = mx.array(spectrum)
        x = self.encode(device.view(mx.float32))
        for block in self.blocks:
            x = block(x)
        return np.asarray(self.decode(x, device))

    def _chunk(self, audio):
        length = len(audio)
        mode = "reflect" if length > FFT // 2 else "constant"
        padded = np.pad(audio, FFT // 2, mode=mode)
        frames = np.lib.stride_tricks.sliding_window_view(padded, FFT)[::HOP]
        spectrum = rfft(frames * WINDOW, workers=-1)
        windows = irfft(self._correction(spectrum), workers=-1) * WINDOW
        center = slice(FFT // 2, FFT // 2 + length)
        return audio + _overlap_add(windows)[center] / _window_weight(len(frames))[center]

    def enhance(self, samples: np.ndarray) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float32)
        if len(samples) <= CHUNK:
            return self._chunk(samples)
        starts = [*range(0, len(samples) - CHUNK, CHUNK - OVERLAP), len(samples) - CHUNK]
        out = np.zeros_like(samples)
        weight = np.zeros_like(samples)
        fade = np.linspace(0, 1, OVERLAP, dtype=np.float32)
        for i, start in enumerate(starts):
            window = np.ones(CHUNK, np.float32)
            if i:
                offset = starts[i - 1] + CHUNK - OVERLAP - start
                window[:offset] = 0
                window[offset : offset + OVERLAP] = fade
            if start != starts[-1]:
                window[-OVERLAP:] = fade[::-1]
            span = slice(start, start + CHUNK)
            out[span] += self._chunk(samples[span]) * window
            weight[span] += window
        return out / weight
