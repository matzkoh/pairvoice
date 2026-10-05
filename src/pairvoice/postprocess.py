"""合成した音声の後処理。頭と末尾のゴミを切り（trim）、音量を揃える（normalize）。

頭と末尾には「んぁ」「ふぅ」のような声や息、プツッという音が付くことがある。

Irodori-TTS は文から見積もった長さを埋めるまで生成するので、見積もりが本文より長いと
余りを短い発声で埋める。mlx-audio は潜在表現で末尾の無音を切るが、声は無音ではないので
残る。頭にも、本文の前に間を空けて短い音が出ることがある。

どちらも本文との間に間（GAP_SECONDS 以上）を空けて現れる短いかたまりなので、それを
落とす。本文の語頭・語尾は間を空けずに続くので巻き込まない。頭は「次に、」のような
短い語の後に間が空く本文もあるので、末尾より短いものだけを落とす。
"""

from __future__ import annotations

import numpy as np

FRAME_SECONDS = 0.01
# 最大音量からこれだけ下までを「音がある」とみなす
FLOOR_DB = 35.0
# これより短い切れ目は同じかたまりとみなす（子音の閉鎖など）
MERGE_SECONDS = 0.08
# 本文とゴミの間の、最小の間
GAP_SECONDS = 0.15
# ゴミとみなすかたまりの、最大の長さ
MAX_HEAD_SECONDS = 0.12
MAX_TAIL_SECONDS = 0.5
# 末尾はゴミが2つ続くこともあるので、落とすのはこの回数まで
MAX_TAIL_DROPS = 2
# 切った後に残す無音と、プツッと鳴らないためのフェード
HEAD_PAD_SECONDS = 0.05
TAIL_PAD_SECONDS = 0.1
FADE_SECONDS = 0.03


def _segments(samples: np.ndarray, hop: int) -> list[list[int]]:
    """音のあるかたまりを [開始, 終了) のフレーム番号で返す。"""
    frames = len(samples) // hop
    if frames == 0:
        return []
    power = (samples[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)
    db = 10 * np.log10(power + 1e-12)
    voiced = db > db.max() - FLOOR_DB
    edges = np.flatnonzero(np.diff(np.r_[0, voiced.astype(np.int8), 0]))
    merge = MERGE_SECONDS / FRAME_SECONDS
    merged: list[list[int]] = []
    for start, end in edges.reshape(-1, 2).tolist():
        if merged and start - merged[-1][1] < merge:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged


def trim(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    hop = max(1, int(sample_rate * FRAME_SECONDS))
    segments = _segments(samples, hop)
    if not segments:
        return samples
    gap = GAP_SECONDS / FRAME_SECONDS

    # 頭: 最初の「間」より前のかたまり群が短ければ1回だけ落とす
    k = 0
    while k + 1 < len(segments) and segments[k + 1][0] - segments[k][1] < gap:
        k += 1
    if (
        k + 1 < len(segments)
        and segments[k][1] - segments[0][0] <= MAX_HEAD_SECONDS / FRAME_SECONDS
    ):
        segments = segments[k + 1 :]

    # 末尾: 最後の「間」より後ろのかたまり群が短ければ落とす
    for _ in range(MAX_TAIL_DROPS):
        k = len(segments) - 1
        while k > 0 and segments[k][0] - segments[k - 1][1] < gap:
            k -= 1
        if k == 0 or segments[-1][1] - segments[k][0] > MAX_TAIL_SECONDS / FRAME_SECONDS:
            break
        segments = segments[:k]

    start = max(0, segments[0][0] * hop - int(sample_rate * HEAD_PAD_SECONDS))
    end = min(len(samples), segments[-1][1] * hop + int(sample_rate * TAIL_PAD_SECONDS))
    trimmed = samples[start:end].copy()
    fade = min(len(trimmed) // 2, int(sample_rate * FADE_SECONDS))
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, dtype=trimmed.dtype)
        if start > 0:
            trimmed[:fade] *= ramp
        trimmed[-fade:] *= ramp[::-1]
    return trimmed


# 声の部分の RMS をこの値に揃え、ピークは PEAK_CEILING_DB を超えないように抑える。
# モデルの出力は参照音声の音量に引きずられるので、プロファイルを変えても同じ音量で
# 鳴るようにする。ピークは 0dBFS に張り付くことがあり、そのままだと書き出しで割れる
TARGET_RMS_DB = -16.0
PEAK_CEILING_DB = -1.0
# ほぼ無音の出力を雑音ごと持ち上げないための上限
MAX_GAIN_DB = 12.0


def normalize(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    hop = max(1, int(sample_rate * FRAME_SECONDS))
    segments = _segments(samples, hop)
    if not segments:
        return samples
    voiced = np.concatenate([samples[start * hop : end * hop] for start, end in segments])
    rms = float(np.sqrt(np.mean(voiced**2)))
    peak = float(np.abs(samples).max())
    if rms == 0.0 or peak == 0.0:
        return samples
    gain_db = min(
        TARGET_RMS_DB - 20 * np.log10(rms),
        PEAK_CEILING_DB - 20 * np.log10(peak),
        MAX_GAIN_DB,
    )
    return samples * np.float32(10 ** (gain_db / 20))
