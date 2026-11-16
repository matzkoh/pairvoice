"""合成した音声の後処理。前置きを切り（cut_lead）、頭と末尾のゴミを切り（trim）、音量を揃える（normalize）。

頭と末尾には「んぁ」「ふぅ」のような声や息、プツッという音が付くことがある。

Irodori-TTS は文から見積もった長さを埋めるまで生成するので、見積もりが本文より長いと
余りを短い発声で埋める。mlx-audio は潜在表現で末尾の無音を切るが、声は無音ではないので
残る。頭にも、本文の前に間を空けて短い音が出ることがある。

どちらも本文との間に間（GAP_SECONDS 以上）を空けて現れる短いかたまりなので、それを
落とす。本文の語頭・語尾は間を空けずに続くので巻き込まない。頭は「次に、」のような
短い語の後に間が空く本文もあるので、末尾より短いものだけを落とす。

合成しながら鳴らすときは、全体が届く前に同じ切り方を決める（StreamCut）。「音がある」の
基準は全体の最大音量から決まるので、頭の切り方は、この先どんな大きさの音が来ても変わらないと
分かった時点で決め、末尾は全部届いてから決める。
"""

from __future__ import annotations

from itertools import pairwise

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


def _frame_db(samples: np.ndarray, hop: int) -> np.ndarray:
    """フレームごとの音量（dB）。"""
    frames = len(samples) // hop
    power = (samples[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1)
    return 10 * np.log10(power + 1e-12)


def _segments(samples: np.ndarray, hop: int) -> list[list[int]]:
    """音のあるかたまりを [開始, 終了) のフレーム番号で返す。"""
    db = _frame_db(samples, hop)
    return _runs(db, float(db.max())) if len(db) else []


def _runs(db: np.ndarray, loudest: float) -> list[list[int]]:
    """loudest を最大音量とみなしたときの、音のあるかたまり。"""
    voiced = db > loudest - FLOOR_DB
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
    bounds = _trim_bounds(samples, sample_rate)
    if bounds is None:
        return samples
    start, end = bounds
    return _faded(samples, start, end, sample_rate, fade_in=start > 0)


def _trim_bounds(samples: np.ndarray, sample_rate: int) -> tuple[int, int] | None:
    """trim が残す範囲 [開始, 終了)。音が無ければ None。"""
    hop = max(1, int(sample_rate * FRAME_SECONDS))
    segments = _segments(samples, hop)
    if not segments:
        return None
    gap = GAP_SECONDS / FRAME_SECONDS

    # 頭: 最初の「間」より前のかたまり群が短ければ1回だけ落とす
    k, short = _head_group(segments)
    if short and k + 1 < len(segments):
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
    return start, end


def _head_group(segments: list[list[int]]) -> tuple[int, bool]:
    """最初の「間」より前のかたまり群の最後の番号と、その群が頭のゴミとみなす短さか。"""
    gap = GAP_SECONDS / FRAME_SECONDS
    k = 0
    while k + 1 < len(segments) and segments[k + 1][0] - segments[k][1] < gap:
        k += 1
    return k, segments[k][1] - segments[0][0] <= MAX_HEAD_SECONDS / FRAME_SECONDS


def _ramp(length: int, dtype) -> np.ndarray:
    """フェードの傾き（0 から 1）。"""
    return np.linspace(0.0, 1.0, length, dtype=dtype)


def _faded(
    samples: np.ndarray, start: int, end: int, sample_rate: int, *, fade_in: bool
) -> np.ndarray:
    """[start, end) を切り出し、プツッと鳴らないよう端をフェードする。"""
    trimmed = samples[start:end].copy()
    fade = min(len(trimmed) // 2, int(sample_rate * FADE_SECONDS))
    if fade:
        ramp = _ramp(fade, trimmed.dtype)
        if fade_in:
            trimmed[:fade] *= ramp
        trimmed[-fade:] *= ramp[::-1]
    return trimmed


# 前置き（tts.LEAD_TEXT）とみなす長さ。話し始めから測る。前置きは 0.15〜0.66 秒で
# 読み終わり、その後に 0.13〜0.95 秒の間が空いた（声11人 × 12文 × seed 2 の 264 本）。
# 話し始めまでの無音は声によって 0〜1 秒とばらつくので、ファイルの頭からは測らない。
# 前置きが本文とつながって読まれると最初のかたまりが長くなるので、上限を超えたら諦める
# （本文の中の間で切らないため）
CUT_LEAD_MIN_SECONDS = 0.15
CUT_LEAD_MAX_SECONDS = 0.8
CUT_LEAD_GAP_SECONDS = 0.1


def cut_lead(samples: np.ndarray, sample_rate: int) -> np.ndarray | None:
    """前置きと、その後の間を切り落とす。間が見つからなければ None（前置きなしで合成し直す）。"""
    hop = max(1, int(sample_rate * FRAME_SECONDS))
    cut = _lead_cut(_segments(samples, hop), hop, sample_rate)
    return None if cut is None else samples[cut:]


def _lead_cut(segments: list[list[int]], hop: int, sample_rate: int) -> int | None:
    """前置きの後の、本文が始まる位置（サンプル）。前置きの後に間が無ければ None。"""
    if not segments:
        return None
    origin = segments[0][0]
    for (_, lead_end), (start, _) in pairwise(segments):
        lead = (lead_end - origin) * FRAME_SECONDS
        if (
            CUT_LEAD_MIN_SECONDS <= lead <= CUT_LEAD_MAX_SECONDS
            and (start - lead_end) * FRAME_SECONDS >= CUT_LEAD_GAP_SECONDS
        ):
            # 本文の弱い語頭（息・無声子音）を残すため、trim と同じだけ手前から残す
            return max(0, start * hop - int(sample_rate * HEAD_PAD_SECONDS))
    return None


# 声の部分の RMS をこの値に揃え、ピークは PEAK_CEILING_DB を超えないように抑える。
# モデルの出力は参照音声の音量に引きずられるので、プロファイルを変えても同じ音量で
# 鳴るようにする。ピークは 0dBFS に張り付くことがあり、そのままだと書き出しで割れる
TARGET_RMS_DB = -16.0
PEAK_CEILING_DB = -1.0
# ほぼ無音の出力を雑音ごと持ち上げないための上限
MAX_GAIN_DB = 12.0


def voiced_rms_db(samples: np.ndarray, sample_rate: int) -> float | None:
    """声の部分（音のあるかたまり）の RMS（dBFS）。音が無ければ None。"""
    hop = max(1, int(sample_rate * FRAME_SECONDS))
    segments = _segments(samples, hop)
    if not segments:
        return None
    voiced = np.concatenate([samples[start * hop : end * hop] for start, end in segments])
    rms = float(np.sqrt(np.mean(voiced**2)))
    return 20 * float(np.log10(rms)) if rms > 0.0 else None


def level_gain_db(samples: np.ndarray, sample_rate: int) -> float | None:
    """声の部分の RMS を TARGET_RMS_DB に揃える倍率（dB）。ピークは見ない。音が無ければ None。"""
    rms_db = voiced_rms_db(samples, sample_rate)
    return None if rms_db is None else min(TARGET_RMS_DB - rms_db, MAX_GAIN_DB)


def normalize(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    gain_db = level_gain_db(samples, sample_rate)
    peak = float(np.abs(samples).max())
    if gain_db is None or peak == 0.0:
        return samples
    gain_db = min(gain_db, PEAK_CEILING_DB - 20 * np.log10(peak))
    return samples * np.float32(10 ** (gain_db / 20))


# --- 合成しながら鳴らす ---

# この先に来る音が、いままでの最大よりどれだけ大きくなりうるかの見積もり（dB）。届いた波形の
# 個数ごとに持ち、最後の値はそれ以降にも使う。373 本で測った実際の差は、1個目（1.84 秒）までで
# 最大 7.6、2個目までで 4.8、3個目までで 3.2
HEADROOM_DB = (8.0, 5.0, 4.0)
HEADROOM_STEP_DB = 1.0
# 見積もりの幅の中で、鳴らし始める位置がこれより開くうちは決めない
START_TOLERANCE_SECONDS = 0.02
# 末尾のゴミは全部届いてから決めるので、終わりのこの長さは届き切るまで出さない。
# 373 本で、trim が決めた終わりから生の終わりまでは最大 1.15 秒
TAIL_HOLD_SECONDS = 1.5


def _head_start(segments: list[list[int]], hop: int, sample_rate: int) -> int | None:
    """trim が頭のゴミを落とした後の、本文の始まり（サンプル）。この先の音しだいなら None。

    segments は途中までの音のもの。頭のかたまりが MAX_HEAD_SECONDS より長ければ、先を見なくても
    落とさないと決まる。短いときは、間の後に次のかたまりが見えるまで決まらない。
    """
    if not segments:
        return None
    k, short = _head_group(segments)
    if not short:
        first = segments[0]
    elif k + 1 < len(segments):
        first = segments[k + 1]
    else:
        return None
    return max(0, first[0] * hop - int(sample_rate * HEAD_PAD_SECONDS))


def _stream_start(
    samples: np.ndarray, sample_rate: int, lead: bool, headroom_db: float
) -> tuple[int, int] | None:
    """途中までの音から、(前置きを切る位置, 鳴らし始める位置) を決める。まだ決められなければ None。

    cut_lead と trim は「音がある」の基準を全体の最大音量から決める。途中までの最大で判定すると、
    後からもっと大きい音が来たときに、小さな息やゴミの扱いが変わって切る位置がずれる。この先の
    最大が、いまの最大から headroom_db 上までのどこであっても同じ位置になるときだけ決める。
    """
    hop = max(1, int(sample_rate * FRAME_SECONDS))
    db = _frame_db(samples, hop)
    if not len(db):
        return None
    top = float(db.max())

    def start_if(loudest: float) -> tuple[int, int] | None:
        cut = _lead_cut(_runs(db, max(top, loudest)), hop, sample_rate) if lead else 0
        if cut is None:
            return None
        body = _frame_db(samples[cut:], hop)
        if not len(body):
            return None
        head = _head_start(_runs(body, max(float(body.max()), loudest)), hop, sample_rate)
        return None if head is None else (cut, cut + head)

    # この先の最大が、いまの本文の最大より小さい場合から順に試す
    found = start_if(-np.inf)
    if found is None:
        return None
    candidates = [found]
    loudest = float(_frame_db(samples[found[0] :], hop).max())
    while loudest <= top + headroom_db:
        found = start_if(loudest)
        if found is None:
            return None
        candidates.append(found)
        loudest += HEADROOM_STEP_DB
    starts = [start for _, start in candidates]
    if max(starts) - min(starts) > START_TOLERANCE_SECONDS * sample_rate:
        return None
    # 語頭を削らないよう、いちばん手前を選ぶ
    return min(candidates, key=lambda found: found[1])


class StreamCut:
    """合成の途中で届く生の波形から、鳴らしてよい分を順に切り出す。

    出すのは trim(cut_lead(全体)) と同じ範囲。頭の切り方が先に決まれば、届いた分から出していく。
    決まらないまま全部届いたら何も出さない（呼び手が全体を見て切る）。
    """

    def __init__(self, sample_rate: int, *, lead: bool) -> None:
        self._rate = sample_rate
        self._lead = lead
        self._raw = np.zeros(0, np.float32)
        self._pushed = 0
        # 前置きを切った位置（前置きが無ければ 0）。出し始めるまでは None
        self.cut: int | None = None
        self._start = 0
        self._released = 0

    def push(self, wave: np.ndarray, remaining: int) -> np.ndarray | None:
        """届いた波形を足し、新しく出せる分を返す。無ければ None。remaining はこのあと届くサンプル数。"""
        self._raw = np.concatenate([self._raw, wave])
        self._pushed += 1
        if self.cut is None:
            if remaining == 0:
                return None
            headroom = HEADROOM_DB[min(self._pushed, len(HEADROOM_DB)) - 1]
            decided = _stream_start(self._raw, self._rate, self._lead, headroom)
            if decided is None:
                return None
            self.cut, self._start = decided
            self._released = self._start
        if remaining == 0:
            return self._rest(self.cut)
        held = len(self._raw) + remaining - int(TAIL_HOLD_SECONDS * self._rate)
        return self._release(self.cut, min(len(self._raw), held))

    def _release(self, cut: int, end: int) -> np.ndarray | None:
        if end <= self._released:
            return None
        piece = self._raw[self._released : end].copy()
        fade = int(self._rate * FADE_SECONDS)
        # trim と同じく、頭を切ったときだけフェードインする
        if self._start > cut and self._released < self._start + fade:
            offset = self._released - self._start
            count = min(fade - offset, len(piece))
            piece[:count] *= _ramp(fade, piece.dtype)[offset : offset + count]
        self._released = end
        return piece

    def _rest(self, cut: int) -> np.ndarray | None:
        """まだ出していない残り。末尾は全体を見て、trim と同じに決める。"""
        bounds = _trim_bounds(self._raw[cut:], self._rate)
        end = len(self._raw) if bounds is None else cut + bounds[1]
        whole = _faded(
            self._raw, self._start, max(end, self._start), self._rate, fade_in=self._start > cut
        )
        rest = whole[self._released - self._start :]
        return rest if len(rest) else None
