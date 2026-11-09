"""読み上げを2ch にして、頭の外から聴こえるようにする。

鳴らす直前にモノラルの wav を2ch にする。保存する wav はモノラルのままで、studio・聞き取り・
記録は影響を受けない。やり方は2つある。

どれにするかは `config.toml` の `[playback] spatial` で決める（`off` なら2ch にせずそのまま鳴らす）。

- 頭の外で鳴らす（`outside`、既定）: 声を正面に置き、部屋の反射と残響を足す。
  ヘッドホンで声が頭の中に貼りつくのは、左右の耳に届く音が同じだからである。声は正面のまま、
  左右それぞれ別の方位から届く反射と、左右で無相関な残響で、両耳の音をずらす
- 耳元で動かす（`orbit`、隠し機能）: `[experimental] spatial = true` のときだけメニューに出る。
  通る点・速さ・近さを鳴らすたびにくじで決め、声が耳元を行き来する

頭の形を測ったデータ（HRTF）は使わず、遠い側の耳を遅らせ（最大 0.63ms）、小さくし、
こもらせる（頭の陰で高い音が届かない）だけで左右を作る。近さは低音（近接効果）と
左右の差の大きさで出す。
"""

from __future__ import annotations

import wave
from collections.abc import Callable
from functools import cache
from pathlib import Path

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt

from .wav import pcm_wav

SUFFIX = ".spatial.wav"

# 両耳の時間差の最大（頭の幅から決まる）
MAX_ITD_SECONDS = 0.00063
# 遠い側の耳に届く音の上限（頭の陰）
SHADOW_HZ = 1800
# 近接効果で持ち上げる低音（この周波数より下を、近さ1で約 +6dB）
PROXIMITY_HZ = 250
PEAK = 0.9

# --- 頭の外で鳴らす ---
# 想定は、吸音を少し施した書斎（家具の入った書斎と録音スタジオの中間）で 1m 先（隣の席）から
# 話しかけられる声。残響は短く暗い。残響が長く明るいと、家具の無いがらんとした部屋に聞こえる。
# 響きを減らしすぎると、左右の耳に届く音のずれも減り、声が頭の中に戻る
# 声の方位（度、正が右）
OUTSIDE_AZIMUTH = 0.0
# 壁からの反射の数と、直接音から遅れて届く範囲（ミリ秒）
REFLECTIONS = 12
REFLECTION_MS = (2.5, 20.0)
# 反射は壁や家具で高い音を失う
WALL_HZ = 3500
# 残響が始まる時刻と、-60dB まで減る長さ。長いほど広い部屋になり、言葉は聞き取りにくくなる
TAIL_START_MS = 18.0
RT60_SECONDS = 0.1
TAIL_HZ = 2250
# 直接音に対する、反射と残響それぞれのエネルギー（dB）。上げるほど遠く、頭の外に出るが、こもる
REFLECTIONS_DB = -14.0
TAIL_DB = -23.0
# 部屋の形はくじで決めるが、鳴らすたびに部屋が変わらないよう種を固定する
ROOM_SEED = 20261110

# --- 耳元で動かす ---
# 通る点1つあたりの長さ。短い文で行き来しすぎないよう、長さに合わせて点の数を決める
SECONDS_PER_POINT = 2.5
MIN_POINTS = 2
MAX_POINTS = 5


def _lowpass(x: np.ndarray, hz: float, rate: int) -> np.ndarray:
    return sosfilt(butter(2, min(hz, rate * 0.45), fs=rate, output="sos"), x, axis=0)


def _ears(azimuth: np.ndarray | float, near: np.ndarray | float = 0.0):
    """左, 右の順に (遠い側への寄り, 音量の倍率) を返す。"""
    sin = np.sin(np.radians(azimuth))
    for side in (-1, 1):
        toward = np.clip(side * sin, 0, 1)
        away = np.clip(-side * sin, 0, 1)
        gain = 10 ** (((4 + 3 * near) * toward - (8 + 6 * near) * away) / 20)
        yield away, gain


def _normalize(stereo: np.ndarray) -> np.ndarray:
    peak = np.abs(stereo).max()
    return stereo / peak * PEAK if peak > 0 else stereo


@cache
def room(rate: int) -> np.ndarray:
    """頭の外で鳴らすための、両耳それぞれの部屋の響き（(長さ, 2) のインパルス応答）。

    直接音・壁からの反射・残響の3つを足す。反射はそれぞれ別の方位から届くので左右の耳で
    時刻と大きさがずれ、残響は左右で無相関にする。この左右のずれが、声を頭の外に出す。
    """
    rng = np.random.default_rng(ROOM_SEED)
    tail_start = int(TAIL_START_MS / 1000 * rate)
    length = tail_start + int(RT60_SECONDS * rate)
    itd = MAX_ITD_SECONDS * rate

    def arrivals(at: np.ndarray, azimuth: np.ndarray, gain: np.ndarray) -> np.ndarray:
        # 頭の陰を通らない分（bright）と通る分（dark）を分けて積み、あとで dark だけこもらせる
        bright = np.zeros((length, 2))
        dark = np.zeros((length, 2))
        for ch, (away, ear_gain) in enumerate(_ears(azimuth)):
            index = np.round(at + away * itd).astype(int)
            np.add.at(bright[:, ch], index, gain * ear_gain * (1 - away))
            np.add.at(dark[:, ch], index, gain * ear_gain * away)
        return bright + _lowpass(dark, SHADOW_HZ, rate)

    direct = arrivals(np.zeros(1), np.array([OUTSIDE_AZIMUTH]), np.ones(1))

    delays = np.sort(rng.uniform(*REFLECTION_MS, REFLECTIONS)) / 1000 * rate
    # 遅く届く反射ほど遠回りしているので小さい
    reflections = arrivals(
        delays,
        rng.uniform(-180, 180, REFLECTIONS),
        rng.uniform(0.6, 1.0, REFLECTIONS) / (1 + delays / rate * 100),
    )
    reflections = _lowpass(reflections, WALL_HZ, rate)

    t = np.arange(length - tail_start) / rate
    envelope = np.exp(-6.91 * t / RT60_SECONDS) * np.minimum(t / 0.005, 1)
    tail = np.zeros((length, 2))
    tail[tail_start:] = (
        _lowpass(rng.standard_normal((len(t), 2)), TAIL_HZ, rate) * envelope[:, None]
    )

    energy = (direct**2).sum()

    def scaled(part: np.ndarray, db: float) -> np.ndarray:
        return part * np.sqrt(energy * 10 ** (db / 10) / (part**2).sum())

    return direct + scaled(reflections, REFLECTIONS_DB) + scaled(tail, TAIL_DB)


def outside(samples: np.ndarray, rate: int) -> np.ndarray:
    """モノラルを、正面の少し離れたところで鳴る (frames + 響きの長さ - 1, 2) の2ch にする。"""
    return _normalize(fftconvolve(samples[:, None], room(rate), axes=0))


def path(frames: int, rate: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """サンプルごとの方位（度、-90 が左耳・90 が右耳）と近さ（0.5〜1）をくじで決める。

    端の点は耳元に置き、途中の点は反対の耳か正面寄りで折り返す。点にとどまる長さと
    点から点へ移る長さもくじで決め、cos でなめらかにつなぐ。
    """
    count = int(np.clip(1 + round(frames / rate / SECONDS_PER_POINT), MIN_POINTS, MAX_POINTS))
    side = rng.choice([-1, 1])
    points = []  # (方位, 近さ)
    for i in range(count):
        at_ear = i in (0, count - 1) or rng.random() < 0.6
        points.append(
            (side * (rng.uniform(55, 88) if at_ear else rng.uniform(-10, 40)), rng.uniform(0.5, 1))
        )
        if rng.random() < 0.75:
            side = -side
    hold = rng.uniform(0.3, 1.5, count)
    move = rng.uniform(0.6, 2.0, count - 1)
    # 区切り: 点0にとどまる, 移る, 点1にとどまる, … 点の値は「とどまる」区間の両端で同じ
    spans = np.ravel(np.column_stack([hold[:-1], move]))
    knots = np.cumsum(np.r_[0.0, spans, hold[-1]])
    knots = knots / knots[-1] * (frames - 1)
    eased = _ease(np.repeat(np.array(points), 2, axis=0), knots, np.arange(frames, dtype=float))
    return eased[:, 0], eased[:, 1]


def _ease(values: np.ndarray, knots: np.ndarray, t: np.ndarray) -> np.ndarray:
    """区切り knots での値 values（行ごと）を、区切りの間は cos でなめらかにつなぐ。"""
    j = np.clip(np.searchsorted(knots, t, side="right") - 1, 0, len(knots) - 2)
    width = np.maximum(knots[j + 1] - knots[j], 1e-9)
    u = (1 - np.cos(np.pi * np.clip((t - knots[j]) / width, 0, 1))) / 2
    return values[j] * (1 - u[:, None]) + values[j + 1] * u[:, None]


def render(samples: np.ndarray, rate: int, azimuth: np.ndarray, near: np.ndarray) -> np.ndarray:
    """モノラルを、方位と近さがサンプルごとに変わる (frames, 2) の2ch にする。"""
    x = samples + _lowpass(samples, PROXIMITY_HZ, rate) * near
    shadow = _lowpass(x, SHADOW_HZ, rate)
    index = np.arange(len(x), dtype=float)
    channels = []
    for away, gain in _ears(azimuth, near):
        signal = (1 - away) * x + away * shadow
        delay = away * MAX_ITD_SECONDS * rate
        channels.append(np.interp(index - delay, index, signal, left=0.0) * gain)
    return _normalize(np.stack(channels, axis=1))


def orbit(samples: np.ndarray, rate: int, rng: np.random.Generator | None = None) -> np.ndarray:
    """モノラルを、耳元を行き来する (frames, 2) の2ch にする。"""
    azimuth, near = path(len(samples), rate, rng or np.random.default_rng())
    return render(samples, rate, azimuth, near)


PLACEMENTS: dict[str, Callable[[np.ndarray, int], np.ndarray]] = {
    "outside": outside,
    "orbit": orbit,
}


def spatialize(source: Path, place: Callable[[np.ndarray, int], np.ndarray]) -> Path:
    """16bit モノラルの wav を place で2ch にして隣に書き、その場所を返す。"""
    with wave.open(str(source)) as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError(f"16bit モノラルではありません: {source.name}")
        rate = reader.getframerate()
        pcm = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
    samples = pcm.astype(np.float64) / 32768.0
    target = source.with_name(source.stem + SUFFIX)
    target.write_bytes(pcm_wav(place(samples, rate), rate))
    return target
