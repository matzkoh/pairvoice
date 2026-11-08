"""読み上げを耳元で左右に動かす（隠し機能）。

`config.toml` の `[experimental] spatial = true` のときだけ、メニューバーに入り切りの項目が
出る。入れると、鳴らす直前にモノラルの wav を2ch にし、文のあいだに声が耳元を行き来する。
動き方（通る点・速さ・近さ）は鳴らすたびにくじで決める。保存する wav はモノラルのままで、
studio・聞き取り・記録は影響を受けない。

頭の形を測ったデータ（HRTF）は使わず、遠い側の耳を遅らせ（最大 0.63ms）、小さくし、
こもらせる（頭の陰で高い音が届かない）だけで左右を作る。近さは低音（近接効果）と
左右の差の大きさで出す。
"""

from __future__ import annotations

import json
import logging
import wave
from pathlib import Path

import numpy as np

from .files import atomic_write
from .wav import pcm_wav

_log = logging.getLogger(__name__)

STATE_FILENAME = "spatial.json"
SUFFIX = ".spatial.wav"

# 両耳の時間差の最大（頭の幅から決まる）
MAX_ITD_SECONDS = 0.00063
# 遠い側の耳に届く音の上限（頭の陰）
SHADOW_HZ = 1800
# 近接効果で持ち上げる低音（この周波数より下を、近さ1で約 +6dB）
PROXIMITY_HZ = 250
# 通る点1つあたりの長さ。短い文で行き来しすぎないよう、長さに合わせて点の数を決める
SECONDS_PER_POINT = 2.5
MIN_POINTS = 2
MAX_POINTS = 5
PEAK = 0.9


class SpatialSwitch:
    """入り切りの状態。データの置き場所に書き、再起動しても残す。"""

    def __init__(self, data_root: Path) -> None:
        self._path = data_root / STATE_FILENAME
        try:
            self.enabled = bool(json.loads(self._path.read_text(encoding="utf-8"))["enabled"])
        except (OSError, ValueError, KeyError, TypeError):
            self.enabled = False

    def set(self, enabled: bool) -> None:
        atomic_write(self._path, json.dumps({"enabled": enabled}))
        self.enabled = enabled


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
    """モノラルを (frames, 2) の2ch にする。"""
    # scipy は読み込みが重いので、使う人だけが払う
    from scipy.signal import butter, sosfilt

    def lowpass(x: np.ndarray, hz: float) -> np.ndarray:
        return sosfilt(butter(2, hz, fs=rate, output="sos"), x)

    x = samples + lowpass(samples, PROXIMITY_HZ) * near
    shadow = lowpass(x, SHADOW_HZ)
    sin = np.sin(np.radians(azimuth))
    index = np.arange(len(x), dtype=float)
    channels = []
    for side in (-1, 1):  # 左, 右
        toward = np.clip(side * sin, 0, 1)
        away = np.clip(-side * sin, 0, 1)
        signal = (1 - away) * x + away * shadow
        gain = 10 ** (((4 + 3 * near) * toward - (8 + 6 * near) * away) / 20)
        delay = away * MAX_ITD_SECONDS * rate
        channels.append(np.interp(index - delay, index, signal, left=0.0) * gain)
    stereo = np.stack(channels, axis=1)
    peak = np.abs(stereo).max()
    return stereo / peak * PEAK if peak > 0 else stereo


def spatialize(source: Path, rng: np.random.Generator | None = None) -> Path:
    """16bit モノラルの wav を、耳元を動く2ch にして隣に書き、その場所を返す。"""
    with wave.open(str(source)) as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError(f"16bit モノラルではありません: {source.name}")
        rate = reader.getframerate()
        pcm = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
    samples = pcm.astype(np.float64) / 32768.0
    azimuth, near = path(len(samples), rate, rng or np.random.default_rng())
    target = source.with_name(source.stem + SUFFIX)
    target.write_bytes(pcm_wav(render(samples, rate, azimuth, near), rate))
    return target
