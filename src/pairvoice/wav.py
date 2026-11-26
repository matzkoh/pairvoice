"""PCM の wav を読む・書く・つなぐ。合成した wav（同じ形式）を1本の参照音声にまとめるのに使う。"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np


class WavFormatError(ValueError):
    pass


def has_wav_header(data: bytes | bytearray) -> bool:
    """RIFF/WAVE の見出しがあるか。中身の妥当性は見ない（mp3 などの取り違えだけを弾く）。"""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def concat_wavs(buffers: list[bytes], gap_seconds: float) -> bytes:
    """同じ形式の wav を、gap_seconds の無音を挟んでつなぐ。形式が違えば WavFormatError。"""
    if not buffers:
        raise WavFormatError("no wav")
    pieces = []
    for data in buffers:
        try:
            with wave.open(io.BytesIO(data)) as reader:
                pieces.append((reader.getparams()[:3], reader.readframes(reader.getnframes())))
        except (wave.Error, EOFError) as broken:
            raise WavFormatError(str(broken)) from broken
    (channels, width, rate), _ = pieces[0]
    if any(params != (channels, width, rate) for params, _ in pieces):
        raise WavFormatError("wav formats differ")
    gap = bytes(round(gap_seconds * rate) * channels * width)
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(gap.join(frames for _, frames in pieces))
    return out.getvalue()


def write_mono(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """-1〜1 の float を 16bit モノラルの wav に書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mono_wav(samples, sample_rate))


def mono_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """-1〜1 の float を 16bit モノラルの wav のバイト列にする。"""
    return pcm_wav(samples, sample_rate)


def pcm_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    """-1〜1 の float（(frames,) ならモノラル、(frames, channels) なら多チャンネル）を 16bit の wav のバイト列にする。"""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1 if pcm.ndim == 1 else pcm.shape[1])
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm.tobytes())
    return out.getvalue()


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    """16bit PCM の wav を -1〜1 の float のモノラル（チャンネルの平均）とサンプルレートで返す。"""
    try:
        with wave.open(str(path)) as reader:
            channels, width, rate = reader.getparams()[:3]
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as broken:
        raise WavFormatError(str(broken)) from broken
    if width != 2:
        raise WavFormatError(f"{width * 8}bit wav")
    samples = np.frombuffer(frames, "<i2").astype(np.float64) / 32768.0
    return samples.reshape(-1, channels).mean(axis=1), rate


# 帯域の切れの判定。caption だけで作る声は、seed しだいで 12kHz（24kHz 収録の上限）や
# それより下で上が切れた、こもった音になる（読ませる文と caption で 0〜9 割）。切れた音は 12.5〜16kHz がほぼ空で
# 200Hz〜1kHz より 80〜95dB 低く、切れていない音は暗い声でも 25〜55dB 低いだけなので、
# 間の -65dB で分ける
_BAND_FRAME = 2048
_SPEECH_RANGE_DB = 30.0
_BAND_LIMIT_DB = -65.0


def band_limited(samples: np.ndarray, sample_rate: int) -> bool:
    """声のある所で 12.5〜16kHz がほぼ空か（上の帯域が切れた、こもった音か）。

    16kHz まで録れないサンプルレートなら切れているとみなす。声が無ければ切れていないとする。
    """
    if sample_rate < 32000:
        return True
    usable = len(samples) // _BAND_FRAME * _BAND_FRAME
    frames = np.asarray(samples[:usable], np.float64).reshape(-1, _BAND_FRAME)
    if not len(frames):
        return False
    level = 10 * np.log10((frames**2).mean(axis=1) + 1e-12)
    speech = frames[level > level.max() - _SPEECH_RANGE_DB]
    power = (np.abs(np.fft.rfft(speech * np.hanning(_BAND_FRAME), axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(_BAND_FRAME, 1 / sample_rate)
    voice = power[(freqs >= 200) & (freqs < 1000)].mean()
    high = power[(freqs >= 12500) & (freqs < 16000)].mean()
    if voice <= 0:
        return False
    return bool(10 * np.log10(high / voice + 1e-30) < _BAND_LIMIT_DB)
