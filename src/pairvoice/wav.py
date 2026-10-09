"""PCM の wav をつなぐ。合成した wav（同じ形式）を1本の参照音声にまとめるのに使う。"""

from __future__ import annotations

import io
import wave


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
