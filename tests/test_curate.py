import json
import shutil
import subprocess
import wave

import numpy as np
import pytest

from pairvoice import curate
from pairvoice.curate import (
    DIARIZE_RATE,
    OUTPUT_RATE,
    CurateError,
    InvalidAudio,
    Material,
    Segment,
    Source,
    TooLittleSpeech,
    build,
    decode,
    find_pieces,
    frame_power,
    merge_segments,
)
from pairvoice.wav import write_mono
from tests.fakes import FakeCurate

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が無い")


def synth(plan, rate, *, noise=0.0005, seed=0):
    """plan の (始まり, 終わり, 振幅, 周波数) に正弦波を置く。振幅 0 は背景音だけを鳴らす区間。"""
    total = max(end for _, end, _, _ in plan) + 1.0
    t = np.arange(int(total * rate)) / rate
    out = np.random.default_rng(seed).normal(0, noise, len(t))
    for start, end, amp, freq in plan:
        span = (t >= start) & (t < end)
        out[span] += amp * np.sin(2 * np.pi * freq * t[span])
    return out.astype(np.float32)


def material(plan, *, right=None, **kwargs):
    """right を渡すと、48kHz の右のチャンネルをその plan にする（省けば左と同じ）。"""
    audio16, left = synth(plan, DIARIZE_RATE, **kwargs), synth(plan, OUTPUT_RATE, **kwargs)
    other = left if right is None else synth(right, OUTPUT_RATE, **kwargs)[: len(left)]
    audio48 = np.stack([left, np.pad(other, (0, len(left) - len(other)))], axis=1)
    return Material(audio16, audio48, [Source("a.wav", 0.0, len(audio16) / DIARIZE_RATE)])


async def run(material, backend=None, enhance=None):
    backend = backend or FakeCurate()
    state = None

    async def diarize(chunk):
        nonlocal state
        segments, state = backend.diarize(chunk, state)
        return segments

    async def enhance_(samples):
        return (enhance or backend.enhance)(samples)

    async def offload(fn, *args):
        return fn(*args)

    return await build(material, diarize, enhance_, offload)


def test_merge_segments_joins_across_chunks_per_speaker():
    merged = merge_segments([Segment(0.0, 59.9, 0), Segment(60.0, 62.0, 0), Segment(59.0, 61.0, 1)])
    assert merged == [Segment(0.0, 62.0, 0), Segment(59.0, 61.0, 1)]


def test_find_pieces_drops_overlap_and_short_and_splits_long():
    # 0 番が 1〜20 秒、1 番が 5〜6 秒に重ね、0 番は 22〜23 秒にも短く話す
    audio = synth([(1, 20, 0.5, 220), (22, 23, 0.5, 220)], DIARIZE_RATE)
    segments = [Segment(1, 20, 0), Segment(5, 6, 1), Segment(22, 23, 0)]
    power = frame_power(audio)
    pieces = find_pieces(segments, 0, power, np.zeros(len(power), bool))
    spans = [(round(p.start, 2), round(p.end, 2)) for p in pieces]
    # 重なりの前の 1〜5 秒と、後の 6〜20 秒。後ろは MAX_PIECE_SECONDS を超えるので切る。
    # 22〜23 秒は短いので落とす
    assert spans == [(1.0, 5.0), (6.0, 10.0), (10.0, 20.0)]
    assert all(p.seconds <= curate.MAX_PIECE_SECONDS for p in pieces)
    assert all(p.background_snr > 40 for p in pieces)


async def test_build_prefers_main_speaker_without_background():
    plan = [
        # 0 番（大きい声）が多く話し、1 番（小さい声）は少し話す
        (1, 6, 0.5, 220),
        (7, 9, 0.2, 330),
        (10, 16, 0.5, 220),
        (17, 23, 0.5, 220),
        (24, 30, 0.5, 220),
        # 31〜50 秒は背景音が大きい（声の間にも鳴る）
        (31, 50, 0.03, 90),
        (32, 37, 0.5, 220),
        (38, 44, 0.5, 220),
    ]
    outcome = await run(material(plan))
    report = outcome.report
    assert report["speakers"][0] == {
        "speaker": 0,
        "seconds": pytest.approx(34, abs=1),
        "chosen": True,
    }
    starts = [p["start"] for p in report["pieces"]]
    # 背景音の大きい 32・38 秒の区間より、静かな区間を選ぶ。18 秒では TARGET_SECONDS に
    # 届かないので、MAX_REFERENCE_SECONDS に収まる4つ目も足す
    assert starts == pytest.approx([1.0, 10.0, 17.0, 24.0], abs=0.05)
    assert curate.TARGET_SECONDS <= report["seconds"] <= curate.MAX_REFERENCE_SECONDS
    assert outcome.sample_rate == OUTPUT_RATE
    assert np.abs(outcome.samples).max() <= 1.0
    # 4つの区間と間の無音
    expected = sum(p["end"] - p["start"] for p in report["pieces"])
    expected += 3 * curate.PIECE_GAP_SECONDS
    assert len(outcome.samples) / OUTPUT_RATE == pytest.approx(expected, abs=0.05)


async def test_build_skips_clipped_pieces():
    plan = [(1, 7, 0.5, 220), (8, 14, 1.2, 220), (15, 21, 0.5, 220)]
    m = material(plan)
    m.audio48 = np.clip(m.audio48, -1, 1)
    outcome = await run(m)
    assert [p["start"] for p in outcome.report["pieces"]] == pytest.approx([1.0, 15.0], abs=0.05)


async def test_build_refuses_when_clean_speech_is_too_short():
    with pytest.raises(CurateError) as raised:
        await run(material([(1, 4, 0.5, 220)]))
    assert isinstance(raised.value, TooLittleSpeech)


async def test_build_refuses_without_speech():
    with pytest.raises(CurateError) as raised:
        await run(material([(1, 4, 0.0, 220)]))
    assert isinstance(raised.value, TooLittleSpeech)


async def test_build_stops_enhancing_once_enough_clean_pieces():
    # 6 秒の区間が 12 個ある。良い区間が TARGET_SECONDS の2倍集まれば残りは除かない
    plan = [(1 + 7 * k, 7 + 7 * k, 0.5, 220) for k in range(12)]
    backend = FakeCurate()
    await run(material(plan), backend)
    assert backend.enhance_calls == 7


@needs_ffmpeg
def test_decode_joins_files_of_any_format(tmp_path):
    write_mono(tmp_path / "a.wav", synth([(0.5, 2.5, 0.5, 220)], 44100), 44100)
    write_mono(tmp_path / "b.wav", synth([(0.5, 1.5, 0.5, 220)], 22050), 22050)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(tmp_path / "b.wav"), str(tmp_path / "b.m4a")],
        check=True,
    )
    work = tmp_path / "work"
    work.mkdir()
    m = decode([("a.wav", tmp_path / "a.wav"), ("b.m4a", tmp_path / "b.m4a")], work)
    assert [s.name for s in m.sources] == ["a.wav", "b.m4a"]
    first, second = m.sources
    assert first.seconds == pytest.approx(3.5, abs=0.01)
    assert second.offset == pytest.approx(first.seconds + curate.FILE_GAP_SECONDS)
    assert second.seconds == pytest.approx(2.5, abs=0.1)
    # 2つの流れは同じ長さで、ファイルの間は無音
    assert len(m.audio48) / OUTPUT_RATE == pytest.approx(len(m.audio16) / DIARIZE_RATE, abs=1e-3)
    gap = m.audio16[int((first.seconds + 0.1) * DIARIZE_RATE) : int(second.offset * DIARIZE_RATE)]
    assert not gap.any()
    assert m.gap_frames().sum() == int(curate.FILE_GAP_SECONDS * 100) * 2
    assert m.source_at(second.offset + 1).name == "b.m4a"
    assert not m.truncated


@needs_ffmpeg
def test_decode_names_the_unreadable_file(tmp_path):
    (tmp_path / "broken.mp3").write_bytes(b"not audio")
    with pytest.raises(CurateError) as raised:
        decode([("broken.mp3", tmp_path / "broken.mp3")], tmp_path)
    assert isinstance(raised.value, InvalidAudio)
    assert "broken.mp3" in str(raised.value)


@needs_ffmpeg
def test_decode_stops_at_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(curate, "MAX_TOTAL_SECONDS", 3.0)
    write_mono(tmp_path / "a.wav", synth([(0.5, 4.0, 0.5, 220)], 16000), 16000)
    m = decode([("a.wav", tmp_path / "a.wav"), ("b.wav", tmp_path / "a.wav")], tmp_path)
    assert [s.name for s in m.sources] == ["a.wav"]
    assert m.sources[0].seconds == pytest.approx(3.0, abs=0.01)
    assert m.truncated


async def test_build_joins_the_denoised_audio(monkeypatch):
    # つなぐ直前の区間が、雑音を除いた音（0.9 倍）であることを見る
    seen = []
    monkeypatch.setattr(curate, "assemble", lambda pieces: seen.extend(pieces) or np.zeros(1))
    plan = [(1, 7, 0.5, 220), (8, 14, 0.5, 220), (15, 21, 0.5, 220)]
    outcome = await run(material(plan, noise=0.0), enhance=lambda x: x * 0.9)
    assert [float(np.abs(p.audio).max()) for p in seen] == pytest.approx([0.45] * 3, abs=0.01)
    # API の応答にそのまま載せるので、numpy の型を混ぜない
    json.dumps(outcome.report)


def test_assemble_levels_each_piece_and_keeps_peaks_under_the_ceiling():
    rate = OUTPUT_RATE
    quiet = synth([(0.1, 3.9, 0.02, 220)], rate, noise=0.0)[: 4 * rate]
    loud = synth([(0.1, 3.9, 0.4, 220)], rate, noise=0.0)[: 4 * rate]
    # 大きい区間に一瞬だけ割れそうな音（破裂音）を入れる
    loud[2 * rate : 2 * rate + 48] = 0.99
    pieces = [curate.Piece(0, 4, 40.0, audio=quiet), curate.Piece(5, 9, 40.0, audio=loud)]
    out = curate.assemble(pieces)
    gap = int(curate.PIECE_GAP_SECONDS * rate)
    first, second = out[: 4 * rate], out[4 * rate + gap :]
    levels = [curate.voiced_rms_db(p, rate) for p in (first, second)]
    # 26 dB 違った2つの区間が、どちらも TARGET_RMS_DB 近くにそろう
    assert levels == pytest.approx([curate.TARGET_RMS_DB] * 2, abs=1.0)
    assert np.abs(out).max() <= 10 ** (curate.PEAK_CEILING_DB / 20) + 1e-6


def test_limit_peaks_only_touches_the_loud_moment():
    rate = OUTPUT_RATE
    x = synth([(0, 2, 0.3, 220)], rate, noise=0.0)
    x[rate : rate + 48] = 1.0
    y = curate.limit_peaks(x, rate)
    assert np.abs(y).max() <= 10 ** (curate.PEAK_CEILING_DB / 20) + 1e-6
    # 0.5 秒前の音は変えない
    assert np.allclose(y[: rate // 2], x[: rate // 2])


async def test_build_takes_the_louder_channel_without_mixing():
    # 左は小さく、右だけに大きく入った声。混ぜると半分の大きさになる
    plan = [(1, 7, 0.5, 220), (8, 14, 0.5, 220), (15, 21, 0.5, 220)]
    m = material(plan, right=[(1, 7, 0.5, 220), (8, 14, 0.5, 220), (15, 21, 0.5, 220)])
    m.audio48[:, 0] *= 0.1
    seen = []

    def enhance(x):
        seen.append(float(np.abs(x).max()))
        return x * 0.999

    await run(m, enhance=enhance)
    assert min(seen) > 0.45


@needs_ffmpeg
def test_decode_keeps_stereo_channels_apart(tmp_path):
    left, right = synth([(0.5, 2.5, 0.5, 220)], 44100), synth([(0.5, 2.5, 0.2, 330)], 44100)
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes((np.stack([left, right], 1) * 32767).astype("<i2").tobytes())
    m = decode([("stereo.wav", path)], tmp_path)
    assert m.audio48.shape[1] == 2
    peaks = np.abs(m.audio48[: int(3 * OUTPUT_RATE)]).max(axis=0)
    assert peaks[0] == pytest.approx(0.5, abs=0.05)
    assert peaks[1] == pytest.approx(0.2, abs=0.05)


def test_find_pieces_keeps_a_turn_across_short_pauses():
    # 同じ人が 0.6 秒の間を挟んで話し続ける。間で割らずに1つの区間にする
    audio = synth([(1, 4, 0.5, 220), (4.6, 8, 0.5, 220)], DIARIZE_RATE)
    segments = [Segment(1, 4, 0), Segment(4.6, 8, 0)]
    power = frame_power(audio)
    pieces = find_pieces(segments, 0, power, np.zeros(len(power), bool))
    assert [(round(p.start, 2), round(p.end, 2)) for p in pieces] == [(1.0, 8.0)]


def test_split_cuts_at_a_real_pause_not_a_dip():
    # 10 ミリ秒だけ途切れる所（子音の閉じ）より、0.3 秒の間で切る
    plan = [(0, 6.0, 0.5, 220), (6.01, 9.0, 0.5, 220), (9.3, 15, 0.5, 220)]
    power = frame_power(synth(plan, DIARIZE_RATE))
    (first, _), (cut, _) = curate._split(0, int(15 / curate.FRAME_SECONDS), power)
    assert first == 0
    assert 9.0 <= cut * curate.FRAME_SECONDS <= 9.3


def test_snap_edges_moves_cuts_into_the_nearest_quiet():
    # 声は 2.0〜6.0 秒。話者分離が 2.3 秒から 5.7 秒と、内側にずれて付けた
    audio = synth([(2.0, 6.0, 0.5, 220)], OUTPUT_RATE, noise=0.0001)
    piece = curate.Piece(2.3, 5.7, 40.0, min_start=1.8, max_end=6.2)
    start, end = curate.snap_edges(piece, audio, 0.0)
    assert start == pytest.approx(1.99, abs=0.02)
    assert end == pytest.approx(6.0, abs=0.02)


def test_snap_edges_stays_off_the_other_speaker():
    # 1.6〜2.0 秒はほかの人の声。そこへは頭を動かさない
    audio = synth([(1.6, 2.0, 0.5, 330), (2.2, 6.0, 0.5, 220)], OUTPUT_RATE, noise=0.0001)
    piece = curate.Piece(2.4, 6.0, 40.0, min_start=2.05, max_end=6.5)
    start, _ = curate.snap_edges(piece, audio, 0.0)
    assert 2.05 <= start <= 2.2


async def test_build_fills_by_rank_even_when_one_piece_stands_out():
    # 1つだけ背景音の無い区間があり、残りは背景音が少し大きい。点の差で切らず、20 秒に届く
    # まで点の高い順に取る
    plan = [
        (1, 7, 0.5, 220),
        (9, 15, 0.5, 220),
        (17, 23, 0.5, 220),
        (25, 31, 0.5, 220),
        (8.5, 32, 0.01, 90),
    ]
    outcome = await run(material(plan))
    assert len(outcome.report["pieces"]) == 4
    assert outcome.report["seconds"] >= curate.TARGET_SECONDS
