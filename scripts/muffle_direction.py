"""こもりの向き（src/pairvoice/muffle/<モデル>.npy）を作る。合成モデルを替えたら作り直す。

caption だけで作る声は seed しだいで 10〜12kHz より上が切れ、張り（2〜5kHz）も弱い、こもった音に
なり、混ぜた声にもそのまま移る。こもっていないテイクと、それを加工した音の組は、声が同じで帯域
だけが違う。組ごとの話者の表現（時間平均）の差を平均すると、その帯域だけを動かす向きになる。

- 上の帯域: もとの音 − 12kHz で切った音
- 張り: もとの音 − 2〜5kHz を 6dB 下げた音

強さ 1 あたり、上の帯域を 1、張りを 1/3 足す（強さ 3 で張り 1。耳で選んだ組み合わせ）。
低音の膨らみを減らす向きは声が痩せて、耳では澄んで聞こえなかった。

caption と seed を固定するので、同じモデルなら同じ向きになる。利用者の声は使わない
（実在の人の声から取った数値を配らない）。

    uv run python scripts/muffle_direction.py
"""

from __future__ import annotations

import dataclasses
import itertools
import sys
import tempfile
from pathlib import Path

import numpy as np

from pairvoice.config import load_config
from pairvoice.tts import MlxAudioBackend, muffle_direction_path
from pairvoice.wav import band_limited, read_mono

TEXT = "設定ファイルを読み直したので、次の読み上げから新しい声に切り替わります。"
# 耳で選んだ向きを作った caption。男性の caption の組は差がほとんど出ず（女性の 1/7 ほど）、
# 混ぜても向きは女性の組とほぼ同じになる（男性の声も、この向きで上の帯域が戻る）
CAPTIONS = (
    "女性の声。声は低め。",
    "女性の声。声の高さは普通、澄んだ声。明るい調子で話す。",
    "女性の声。息まじりの柔らかい声。穏やかで優しい調子で話す。",
    "女性の声。落ち着いた調子で、ややゆっくり話す。",
    "女性の声。丸く温かい声。淡々とした調子で話す。",
)
SEEDS = range(1, 9)
CUTOFF_HZ = 12000.0
TAPER_HZ = 300.0
PRESENCE_DB = -6.0
PRESENCE_RATIO = 1 / 3


def filtered(samples: np.ndarray, sample_rate: int, gain) -> np.ndarray:
    """周波数ごとに gain（倍率）を掛け、16bit の wav に書いたときと同じ値にそろえる。

    こもった声の上の帯域は無音ではなく、16bit に丸めた細かな雑音になっている。完全な無音との差を
    取ると別の向きになり（cos 0.83）、足しても耳では澄んで聞こえなかった。
    """
    freqs = np.fft.rfftfreq(len(samples), 1 / sample_rate)
    out = np.fft.irfft(np.fft.rfft(samples) * gain(freqs), len(samples))
    # wav.pcm_wav で書いて read_mono で読み戻したのと同じ値
    return np.trunc(np.clip(out, -1, 1) * 32767) / 32768


def lowpass(freqs: np.ndarray) -> np.ndarray:
    """CUTOFF_HZ より上を消す。"""
    return np.clip((CUTOFF_HZ - freqs) / TAPER_HZ, 0, 1)


def dull(freqs: np.ndarray) -> np.ndarray:
    """2〜5kHz を PRESENCE_DB 下げる（両側 1 オクターブでなだらかに）。"""
    octaves = np.log2(np.maximum(freqs, 1) / 1000), np.log2(10000 / np.maximum(freqs, 1))
    return 10 ** (PRESENCE_DB * np.clip(np.minimum(*octaves), 0, 1) / 20)


def main() -> int:
    config = load_config()
    with tempfile.TemporaryDirectory() as scratch:
        tts = dataclasses.replace(config.tts, output_dir=Path(scratch) / "generations")
        backend = MlxAudioBackend(tts, data_dir=Path(scratch))
        backend.load()

        def mean_tokens(samples: np.ndarray) -> np.ndarray:
            return backend.speaker_tokens(np.clip(samples, -1, 1)).mean(axis=0)

        high, presence = [], []
        for caption, seed in itertools.product(CAPTIONS, SEEDS):
            result = backend.speak(TEXT, caption=caption, sampler={"rng_seed": seed}, design=True)
            samples, rate = read_mono(result.path)
            if band_limited(samples, rate):
                continue
            plain = mean_tokens(samples)
            high.append(plain - mean_tokens(filtered(samples, rate, lowpass)))
            presence.append(plain - mean_tokens(filtered(samples, rate, dull)))
    out = muffle_direction_path(tts.model)
    out.parent.mkdir(parents=True, exist_ok=True)
    direction = np.mean(high, axis=0) + PRESENCE_RATIO * np.mean(presence, axis=0)
    np.save(out, direction.astype(np.float32))
    print(f"{out}: {len(high)} 本のテイクから作った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
