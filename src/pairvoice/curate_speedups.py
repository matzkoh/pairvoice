"""mlx-audio 0.6.8 の音声クリーンアップで繰り返す演算をまとめる。"""

from functools import partial

import mlx.core as mx


def kaldi_deltas(specgram: mx.array, win_length: int = 5, mode: str = "edge") -> mx.array:
    """前後のフレームから時間差分を取る。すべての窓を1回の和で計算する。"""
    if win_length < 3:
        raise ValueError("win_length must be at least 3")
    radius = (win_length - 1) // 2
    width = radius * 2 + 1
    weights = mx.arange(-radius, radius + 1, dtype=specgram.dtype)
    frames = specgram.reshape(-1, specgram.shape[-1])
    padded = mx.contiguous(
        mx.pad(frames, [(0, 0), (radius, radius)], mode="edge" if mode == "edge" else "constant")
    )
    windows = mx.as_strided(padded, shape=(*frames.shape, width), strides=(padded.shape[1], 1, 1))
    denominator = radius * (radius + 1) * (2 * radius + 1) / 3
    return (mx.sum(windows * weights, axis=-1) / denominator).reshape(specgram.shape)


@partial(mx.compile, shapeless=True)
def _rms_norm(x, weight, scale):
    length = mx.sqrt(mx.sum(x * x, axis=-1, keepdims=True))
    return (x / mx.maximum(length, 1e-12)) * scale * weight


def roformer_norm(norm):
    """元の RMSNorm の下限と演算順で、要素ごとの処理をまとめる。"""
    return partial(_rms_norm, weight=norm.weight, scale=norm.scale)


def roformer_attention(attention):
    """Q、K、V の射影を1回の行列積にまとめ、回転位置埋め込みを専用カーネルで計算する。"""
    weights = mx.concatenate([attention.to_q.weight, attention.to_k.weight, attention.to_v.weight])
    mx.eval(weights)
    heads, dim = attention.heads, attention.dim_head
    norm = roformer_norm(attention.norm)
    to_gates, to_out = attention.to_gates, attention.to_out

    def rotate(x):
        return mx.fast.rope(x, dim, traditional=True, base=10000, scale=1, offset=0)

    def attend(x):
        batch, frames, _ = x.shape
        h = norm(x)
        qkv = (h @ weights.T).reshape(batch, frames, 3, heads, dim).transpose(2, 0, 3, 1, 4)
        out = mx.fast.scaled_dot_product_attention(
            rotate(qkv[0]), rotate(qkv[1]), qkv[2], scale=dim**-0.5
        )
        gates = mx.sigmoid(to_gates(h)).transpose(0, 2, 1)[..., None]
        return to_out((out * gates).transpose(0, 2, 1, 3).reshape(batch, frames, -1))

    return attend
