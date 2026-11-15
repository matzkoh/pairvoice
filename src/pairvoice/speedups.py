"""Irodori-TTS の合成を、出す音を変えずに速める差し替え。mlx-audio 0.6.4 の中身に合わせてある。

音の中身を決める DiT（拡散モデルのサンプラー）の計算には触らない。速めるのは、同じ計算を
省くか、同じ式を速い形で計算し直すところだけ:

- 条件の符号化（encode_conditions_full）: 1回の生成の中で、継続時間の予測とサンプラーが
  同じ入力で2回呼ぶので、2回目は1回目の結果を返す
- 無音の位置探し（_find_silence_point）: 窓ごとに GPU と同期していたのを、numpy でまとめて
  測ってから、しきい値に近い窓だけ元の式で確かめる
- DACVAE のデコード:
  - 重み正規化した重みを、呼ぶたびでなく読み込み時に1回だけ作る
  - 転置畳み込みを行列積に書き直す。MLX は転置畳み込みをゼロを挟んだ入力への畳み込みとして
    計算するので、掛け算の大半がゼロとの積になり遅い（1チャンクの時間の4割を占めていた）
  - snake 活性化を1つのカーネルにまとめる
  - チャンクの間で GPU のキャッシュを捨てない
  - 無音として切り捨てる先のチャンクは解かない（チャンクの切り方とつなぎ方は変えない）

転置畳み込みと snake は浮動小数点の足し合わせの順が変わるので、デコードした波形は 1e-6 程度
ずれる（16bit の wav で高々 1 LSB）。それ以外の差し替えはビット単位で同じ値を返す。
"""

from __future__ import annotations

import functools
import types
from collections.abc import Callable

import mlx.core as mx
import numpy as np

SILENCE_WINDOW = 20
SILENCE_STD = 0.05
SILENCE_MEAN = 0.1
# numpy（float64）と MLX（float32）の測り方の差よりずっと大きく取る。この幅に入った窓だけ元の式で確かめる
_SILENCE_MARGIN = 1e-3


def install(model) -> bool:
    """読み込んだ Irodori-TTS のモデルに差し替えを入れる。形が想定と違えば何もせず False を返す。"""
    from mlx_audio.codec.models.dacvae import codec
    from mlx_audio.tts.models.irodori_tts import irodori_tts

    dit = getattr(model, "model", None)
    dac = getattr(model, "dacvae", None)
    if dit is None or dac is None or not hasattr(dit, "encode_conditions_full"):
        return False
    if not hasattr(irodori_tts, "_find_silence_point") or not hasattr(dac, "_decode_chunked"):
        return False

    _drop_empty_caption(dit)
    _reuse_second_call(dit)
    # 同じ答えを返す関数なので、型の見た目が違うだけ
    irodori_tts._find_silence_point = find_silence_point  # ty: ignore[invalid-assignment]
    _speed_up_decoder(dac.decoder, codec)
    _freeze_weight(dac.quantizer_out_proj, codec)
    dac._decode_chunked = types.MethodType(_decode_chunked, dac)
    return True


def _drop_empty_caption(dit) -> None:
    """空の caption（全部マスクされた caption）を、符号化せずに長さ0の列として返す。

    空の caption も 512 トークン分を符号化し、DiT の注意は毎ステップその 512 個のキーも相手に
    する。マスクされたキーの重みは softmax でちょうど0になり、キーの並びの末尾にあるので、
    除いても前のキーの足し合わせの順は変わらない。継続時間の予測は caption が無いとき
    mx.where で既定のベクトルを選ぶので、長さ0でも同じ値になる。
    """
    original = dit.encode_conditions_full
    dtype = None

    def encode(*args, **kwargs):
        nonlocal dtype
        mask = kwargs.get("caption_mask")
        if args or mask is None or kwargs.get("caption_input_ids") is None or bool(mx.any(mask)):
            return original(*args, **kwargs)
        if dtype is None:
            # 長さ0の列の型が違うと、つないだキーの型が昇格して計算が変わる。最初の1回は元どおり求めて型を知る
            out = original(*args, **kwargs)
            dtype = out[4].dtype
        else:
            out = original(*args, **{**kwargs, "caption_input_ids": None, "caption_mask": None})
        batch = out[0].shape[0]
        empty = mx.zeros((batch, 0, dit.cfg.caption_dim_resolved), dtype=dtype)
        return (*out[:4], empty, mx.zeros((batch, 0), dtype=mx.bool_))

    dit.encode_conditions_full = encode


def _reuse_second_call(dit) -> None:
    """encode_conditions_full が直前と同じ引数（同じオブジェクト）で呼ばれたら、直前の結果を返す。

    1回の生成で継続時間の予測とサンプラーが同じ配列を渡して2回呼ぶ。MLX の配列は書き換え
    られないので、同じオブジェクトなら同じ値になる。2回目で控えを捨て、生成の後まで条件の
    配列を握り続けない（引数を握るのは、解放されたオブジェクトの id の使い回しで取り違えないため）。
    """
    original = dit.encode_conditions_full
    last: list = []

    def encode(*args, **kwargs):
        if last:
            held_args, held_kwargs, out = last.pop()
            if (
                len(args) == len(held_args)
                and all(a is b for a, b in zip(args, held_args, strict=True))
                and kwargs.keys() == held_kwargs.keys()
                and all(kwargs[k] is held_kwargs[k] for k in kwargs)
            ):
                return out
        out = original(*args, **kwargs)
        last.append((args, kwargs, out))
        return out

    dit.encode_conditions_full = encode


def find_silence_point(
    latent: mx.array, window_size: int = SILENCE_WINDOW, std_threshold: float = SILENCE_STD
) -> int:
    """mlx-audio の _find_silence_point と同じ答えを返す。

    元は窓ごとに float() で GPU と同期するので、長い文で数十ミリ秒かかる。全部の窓を numpy で
    まとめて測り、明らかに外れる窓は飛ばし、残りだけを元と同じ式（MLX の float32）で順に確かめる。
    """
    maybe, _ = _silence_masks(latent, window_size, std_threshold)
    candidates = np.flatnonzero(maybe)
    if candidates.size:
        padded = mx.concatenate(
            [latent, mx.zeros((window_size, latent.shape[-1]), dtype=latent.dtype)], axis=0
        )
        for i in candidates:
            window = padded[i : i + window_size]
            if float(window.std()) < std_threshold and abs(float(window.mean())) < SILENCE_MEAN:
                return int(i)
    return int(latent.shape[0])


def silence_upper_bound(latent: mx.array) -> int | None:
    """find_silence_point の答え以上であることが確かな位置。None は「generate() は切り捨てない
    かもしれない」で、そのときは全部を解く。答えそのものは generate() が後で求める。
    """
    maybe, sure = _silence_masks(latent, SILENCE_WINDOW, SILENCE_STD)
    # 先頭の窓が無音なら generate() は切り捨てずに全部を使う
    if maybe[:1].any() or not sure.any():
        return None
    return int(np.argmax(sure))


def _silence_masks(
    latent: mx.array, window_size: int, std_threshold: float
) -> tuple[np.ndarray, np.ndarray]:
    """各位置から始まる窓が、無音かもしれないか（maybe）と、確かに無音か（sure）。

    末尾をゼロで埋めた窓の平均と標準偏差を、累積和の差で float64 で出す。MLX（float32）との
    測り方の差は _SILENCE_MARGIN よりずっと小さいので、sure は元の式でも必ず無音になる。
    """
    values = np.asarray(latent, dtype=np.float64)
    frames, dim = values.shape
    count = window_size * dim
    # 埋めたゼロの行は和に効かないので、範囲の端を frames で止めれば埋めずに済む
    ends = np.minimum(np.arange(frames) + window_size, frames)
    sums = np.concatenate([[0.0], np.cumsum(values.sum(axis=1))])
    squares = np.concatenate([[0.0], np.cumsum(np.einsum("ij,ij->i", values, values))])
    mean = (sums[ends] - sums[:frames]) / count
    mean_sq = (squares[ends] - squares[:frames]) / count
    std = np.sqrt(np.maximum(mean_sq - mean * mean, 0.0))
    abs_mean = np.abs(mean)
    maybe = (std < std_threshold + _SILENCE_MARGIN) & (abs_mean < SILENCE_MEAN + _SILENCE_MARGIN)
    sure = (std < std_threshold - _SILENCE_MARGIN) & (abs_mean < SILENCE_MEAN - _SILENCE_MARGIN)
    return maybe, sure


# --- DACVAE のデコーダー ---------------------------------------------------------------


def _speed_up_decoder(decoder, codec) -> None:
    # 透かしの経路（upsample_group / downsample_group）はデコードで通らないので触らない
    for block in decoder.blocks:
        _swap_snake(block.block_0, codec)
        # 行列積に置き換える層は並べ替えた重みだけを持つ（正規化した重みを別に取っておかない）
        if _is_plain_upsample(block.block_1):
            _to_matmul_transpose(block.block_1)
        else:
            _freeze_weight(block.block_1, codec)
        for unit in (block.block_4, block.block_5, block.block_8):
            _swap_snake(unit.act1, codec)
            _swap_snake(unit.act2, codec)
            _freeze_weight(unit.conv1, codec)
            _freeze_weight(unit.conv2, codec)
    _freeze_weight(decoder.conv_in, codec)
    _swap_snake(decoder.snake_out, codec)
    _freeze_weight(decoder.conv_out, codec)


def _freeze_weight(module, codec) -> None:
    """重み正規化した重みを1回だけ作り、以後はそれを返す。式は元の _get_weight / __call__ と同じ。"""
    if isinstance(module, (codec.WNConv1d, codec.WNConvTranspose1d)):
        if not module.use_weight_norm:
            return
        weight = module._get_weight()
        mx.eval(weight)
        object.__setattr__(module, "_get_weight", lambda: weight)
    elif isinstance(module, codec.QuantizerOutProj):
        weight = module.weight_g * module.weight_v / codec.normalize_weight(module.weight_v)
        mx.eval(weight)
        object.__setattr__(module, "_frozen_weight", weight)
        module.__class__ = _subclass(type(module), _FrozenProj)


def _is_plain_upsample(module) -> bool:
    return (
        module.pad_mode == "none"
        and module.dilation == 1
        and module.kernel_size == 2 * module.stride
        and module.padding == (module.stride + 1) // 2
    )


def _to_matmul_transpose(module) -> None:
    """転置畳み込み（kernel = 2 * stride）を、ずらした入力を横に並べた行列積に置き換える。

    出力の stride 個ずつの塊 j は x[j] @ W[:, :s] と x[j-1] @ W[:, s:] の和になる。
    [x[j], x[j-1]] を並べて1回の行列積で求め、元と同じだけ両端を削る。
    """
    weight = module._get_weight()  # (C_out, 2s, C_in)
    stride = module.stride
    c_out, _, c_in = weight.shape

    def taps(part):
        return mx.transpose(part, (2, 1, 0)).reshape(c_in, stride * c_out)

    stacked = mx.concatenate([taps(weight[:, :stride]), taps(weight[:, stride:])], axis=0)
    mx.eval(stacked)
    object.__setattr__(module, "_stacked_weight", stacked)
    module.__class__ = _subclass(type(module), _MatmulTranspose)


class _MatmulTranspose:
    stride: int
    padding: int
    bias: mx.array | None
    _stacked_weight: mx.array

    def __call__(self, x: mx.array) -> mx.array:
        batch, frames, _ = x.shape
        stride = self.stride
        pairs = mx.concatenate(
            [mx.pad(x, [(0, 0), (0, 1), (0, 0)]), mx.pad(x, [(0, 0), (1, 0), (0, 0)])], axis=-1
        )
        y = (pairs @ self._stacked_weight).reshape(batch, (frames + 1) * stride, -1)
        y = y[:, self.padding : (frames + 1) * stride - self.padding]
        if self.bias is not None:
            y = y + self.bias
        return y


class _FrozenProj:
    bias: mx.array
    _frozen_weight: mx.array

    def __call__(self, x: mx.array) -> mx.array:
        return mx.conv1d(x, self._frozen_weight, stride=1, padding=0) + self.bias


def _swap_snake(module, codec) -> None:
    if isinstance(module, codec.Snake1d):
        object.__setattr__(module, "_snake", _fused_snake(codec.snake))
        module.__class__ = _subclass(type(module), _FusedSnake)


@functools.cache
def _fused_snake(snake):
    return mx.compile(snake, shapeless=True)


class _FusedSnake:
    alpha: mx.array
    _snake: Callable[[mx.array, mx.array], mx.array]

    def __call__(self, x: mx.array) -> mx.array:
        return self._snake(x, self.alpha)


_SUBCLASSES: dict[tuple[type, type], type] = {}


def _subclass(base: type, mixin: type) -> type:
    """インスタンスの __call__ だけを差し替えるための子クラス（__call__ はクラスから引かれる）。"""
    key = (base, mixin)
    if key not in _SUBCLASSES:
        _SUBCLASSES[key] = type(f"{mixin.__name__.lstrip('_')}{base.__name__}", (mixin, base), {})
    return _SUBCLASSES[key]


def _decode_chunked(self, encoded_frames: mx.array, chunk_size: int, overlap: int = 4) -> mx.array:
    """mlx-audio の DACVAE._decode_chunked と同じ切り方・つなぎ方で解く。

    違うのは、チャンクの間で GPU のキャッシュを捨てないことと、無音として切り捨てられる
    位置より先のチャンクを解かないこと。打ち切ったチャンクの末尾（次のチャンクと重ねる所）は
    元と値が違うが、そこは generate() が必ず切り捨てる位置より後ろにある。
    """
    _, _, total_frames = encoded_frames.shape
    cut = silence_upper_bound(mx.transpose(encoded_frames[0], (1, 0)))
    stop = total_frames if cut is None else cut

    encoded_frames = mx.transpose(encoded_frames, (0, 2, 1))
    overlap_samples = overlap * self.hop_length

    chunks = []
    start = 0
    while start < total_frames:
        end = min(start + chunk_size, total_frames)
        emb = self.quantizer_out_proj(encoded_frames[:, start:end, :])
        out = self.decoder(emb)
        out = self.decoder.snake_out(out)
        out = mx.tanh(self.decoder.conv_out(out))
        mx.eval(out)
        chunks.append(out)
        if end >= total_frames:
            break
        start = end - overlap
        if start >= stop:
            break

    if len(chunks) == 1:
        return chunks[0]
    return _crossfade(chunks, overlap_samples)


def _crossfade(chunks: list[mx.array], overlap_samples: int) -> mx.array:
    """mlx-audio の DACVAE._decode_chunked のつなぎ方をそのまま写したもの。"""
    fade_in = mx.linspace(0.0, 1.0, overlap_samples).reshape(1, -1, 1)
    fade_out = mx.linspace(1.0, 0.0, overlap_samples).reshape(1, -1, 1)
    result_parts = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            fade_out_start = chunk.shape[1] - overlap_samples
            result_parts.append(chunk[:, :fade_out_start, :])
            result_parts.append(chunk[:, fade_out_start:, :] * fade_out)
        elif i == len(chunks) - 1:
            result_parts[-1] = result_parts[-1] + chunk[:, :overlap_samples, :] * fade_in
            result_parts.append(chunk[:, overlap_samples:, :])
        else:
            result_parts[-1] = result_parts[-1] + chunk[:, :overlap_samples, :] * fade_in
            result_parts.append(chunk[:, overlap_samples:-overlap_samples, :])
            result_parts.append(chunk[:, -overlap_samples:, :] * fade_out)
    return mx.concatenate(result_parts, axis=1)
