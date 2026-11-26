"""こもりの向き（src/pairvoice/muffle/<モデル>.npz）を作る。合成モデルを替えたら作り直す。

caption だけで作る声は seed しだいで 10〜12kHz より上が切れ、低音が膨らんだ、こもった音になり、
混ぜた声にもそのまま移る。こもっていないテイクと、それを加工した音の組は、声が同じで録音の
具合だけが違う。組ごとの話者の表現（時間平均）の差を平均すると、その具合だけを動かす向きになる。

- high: もとの音 − 10kHz で切った音。足すと上の帯域が戻る（12kHz で切った組より、10kHz から
  切れた声まで埋まる）
- boom: 200Hz より下を 10dB 持ち上げた音 − もとの音。引くと低音の膨らみが減る

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
# 男女・高さ・声質・調子を一通り散らす。片方の性別に寄った向きにしない
CAPTIONS = (
    "女性の声。声の高さは普通、澄んだ声。明るい調子で話す。",
    "女性の声。声は低め、丸く温かい声。落ち着いた調子で話す。",
    "女性の声。息まじりの柔らかい声。穏やかで優しい調子で話す。",
    "女性の声。声は高め、はきはきした声。元気いっぱいの調子で話す。",
    "男性の声。声の高さは普通、はきはきした声。淡々とした調子で話す。",
    "男性の声。声は低め、丸く温かい声。落ち着いた調子で話す。",
    "男性の声。声はとても低い、息まじりの柔らかい声。穏やかで優しい調子で話す。",
    "男性の声。声は高め、張りのある力強い声。明るい調子で話す。",
)
SEEDS = range(1, 7)
CUTOFF_HZ = 10000.0
TAPER_HZ = 300.0
BOOM_BELOW_HZ = 200.0
BOOM_WIDTH_HZ = 150.0
BOOM_GAIN_DB = 10.0


def lowpass(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """CUTOFF_HZ より上を消す。"""
    freqs = np.fft.rfftfreq(len(samples), 1 / sample_rate)
    gain = np.clip((CUTOFF_HZ - freqs) / TAPER_HZ, 0, 1)
    return np.fft.irfft(np.fft.rfft(samples) * gain, len(samples))


def boom(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """BOOM_BELOW_HZ より下をなだらかに持ち上げる（はみ出さないよう大きさをそろえる）。"""
    freqs = np.fft.rfftfreq(len(samples), 1 / sample_rate)
    ramp = np.clip((BOOM_BELOW_HZ + BOOM_WIDTH_HZ - freqs) / (2 * BOOM_WIDTH_HZ), 0, 1)
    out = np.fft.irfft(np.fft.rfft(samples) * 10 ** (BOOM_GAIN_DB * ramp / 20), len(samples))
    return out / max(1.0, float(np.abs(out).max()))


def main() -> int:
    config = load_config()
    with tempfile.TemporaryDirectory() as scratch:
        tts = dataclasses.replace(config.tts, output_dir=Path(scratch) / "generations")
        backend = MlxAudioBackend(tts, data_dir=Path(scratch))
        backend.load()

        def mean_tokens(samples: np.ndarray) -> np.ndarray:
            return backend.speaker_tokens(np.clip(samples, -1, 1)).mean(axis=0)

        high, booms = [], []
        for caption, seed in itertools.product(CAPTIONS, SEEDS):
            result = backend.speak(TEXT, caption=caption, sampler={"rng_seed": seed}, design=True)
            samples, rate = read_mono(result.path)
            if band_limited(samples, rate):
                continue
            plain = mean_tokens(samples)
            high.append(plain - mean_tokens(lowpass(samples, rate)))
            booms.append(mean_tokens(boom(samples, rate)) - plain)
    out = muffle_direction_path(tts.model)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        out,
        high=np.mean(high, axis=0).astype(np.float32),
        boom=np.mean(booms, axis=0).astype(np.float32),
    )
    print(f"{out}: {len(high)} 本のテイクから作った")
    return 0


if __name__ == "__main__":
    sys.exit(main())
