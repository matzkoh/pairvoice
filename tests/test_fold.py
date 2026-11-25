import numpy as np
import pytest

from pairvoice import fold
from pairvoice.fold import RATE, medoids, summarize, trim_silence


def tone(seconds, amp=0.5):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def test_trim_silence_shortens_long_pauses_only():
    silence = lambda s: np.zeros(int(s * RATE), np.float32)  # noqa: E731
    x = np.concatenate([tone(1.0), silence(0.2), tone(1.0), silence(2.0), tone(1.0)])

    trimmed = trim_silence(x)

    # 0.2 秒の間はそのまま、2 秒の無音は 0.3 秒に詰める
    assert len(trimmed) / RATE == np.round(3.0 + 0.2 + fold.MAX_PAUSE_SECONDS, 2)


def test_medoids_are_real_points():
    x = np.random.default_rng(0).normal(size=(500, 4)).astype(np.float32)

    picked = medoids(x, 10)

    assert 1 <= len(picked) <= 10
    assert len(set(picked.tolist())) == len(picked)
    assert picked.max() < len(x)


@pytest.mark.parametrize("iterations", [1, 30])
def test_medoids_use_the_final_center_at_the_iteration_limit_and_at_convergence(
    monkeypatch, iterations
):
    monkeypatch.setattr(fold, "KMEANS_ITERATIONS", iterations)
    x = np.array([[0], [2], [3], [4], [10]], dtype=np.float32)
    # 初期中心は10。更新後の中心3.8にいちばん近いのは4。
    np.testing.assert_array_equal(medoids(x, 1, seed=0), [3])


def test_summarize_leads_with_the_most_central_first_token():
    # 継続時間の予測は先頭の1トークンだけを見るので、先頭は各ブロックの先頭トークンの中心から選ぶ
    states = [
        np.array([[0.0, 0.0], [5.0, 5.0], [6.0, 6.0]]),
        np.array([[1.0, 1.0], [5.0, 6.0], [6.0, 5.0]]),
        np.array([[9.0, 9.0], [5.5, 5.5], [6.5, 6.5]]),
    ]

    tokens, center = summarize([s.astype(np.float32) for s in states])

    np.testing.assert_allclose(tokens[0], [1.0, 1.0])
    # 先頭のほかは、各ブロックの先頭を除いた残りから選ぶ（ここでは数が少ないので全部）
    assert len(tokens) == 1 + 6
    assert tokens.dtype == np.float32
    assert 0 <= center < len(states)


async def test_clean_merges_context_and_only_emits_speech_from_each_source():
    from pairvoice import curate
    from tests.test_curate import offload

    # 前後2秒が重なる・接する3区間、離れた1区間、次のファイルの先頭の1区間。
    intervals = [(0, 1), (2, 3), (7, 8), (14, 15), (16, 17)]
    pieces = [curate.Piece(a, b, 0) for a, b in intervals]
    audio = np.linspace(0.1, 0.9, 18 * RATE, dtype=np.float32)
    material = curate.Material(
        np.empty(0),
        np.stack([audio * 0.5, audio], axis=1),
        [curate.Source("a", 0, 15), curate.Source("b", 16, 2)],
    )
    analysis = curate.Analysis(material, np.empty(0), [], {}, 0, pieces, [])
    inputs = []
    stages = []

    async def enhance(samples):
        inputs.append(samples)
        return -samples

    speech = await fold.clean_pieces(analysis, enhance, offload, lambda *args: stages.append(args))
    for x, (a, b) in zip(inputs, [(0, 10), (12, 15), (16, 18)], strict=True):
        np.testing.assert_array_equal(x, audio[a * RATE : b * RATE])
    for x, (a, b) in zip(speech, intervals, strict=True):
        np.testing.assert_array_equal(x, -audio[a * RATE : b * RATE])
    assert stages == [("clean", 0), ("clean", 10 / 15), ("clean", 13 / 15), ("clean", 1)]


async def test_long_clean_span_limits_each_call_and_discards_context_at_seams():
    from pairvoice import curate
    from tests.test_curate import offload

    # 18秒と34秒の継ぎ目が本人区間の途中に入り、最後は短い窓になる。
    intervals = [(0, 11), (11, 22), (21, 32), (32, 43), (43, 54)]
    audio = np.linspace(0.2, 0.8, 54 * RATE, dtype=np.float32)
    other = np.zeros_like(audio)
    other[: 20 * RATE] = audio[: 20 * RATE] * 1.1
    material = curate.Material(
        np.empty(0), np.column_stack((audio, other)), [curate.Source("a", 0, 54)]
    )
    pieces = [curate.Piece(a, b, 0) for a, b in intervals]
    analysis = curate.Analysis(material, np.empty(0), [], {}, 0, pieces, [])
    bounds = []
    context = round(curate.ENHANCE_CONTEXT_SECONDS * RATE)

    async def enhance(samples):
        start = int(audio.searchsorted(samples[0]))
        end = start + len(samples)
        bounds.append((start, end))
        np.testing.assert_array_equal(samples, audio[start:end])
        out = -samples
        # 人工的に窓の端を壊す。前後の文脈を捨てた結果にこの値が残ってはいけない。
        if start:
            out[:context] = 1
        if end != len(audio):
            out[-context:] = 1
        return out

    progress = []
    speech = await fold.clean_pieces(analysis, enhance, offload, lambda _, f: progress.append(f))
    assert bounds == [(a * RATE, b * RATE) for a, b in [(0, 20), (16, 36), (32, 52), (48, 54)]]
    assert max(b - a for a, b in bounds) <= fold.ENHANCE_SECONDS * RATE
    assert progress == pytest.approx([0, 18 / 54, 34 / 54, 50 / 54, 1])
    for out, (a, b) in zip(speech, intervals, strict=True):
        np.testing.assert_array_equal(out, -audio[a * RATE : b * RATE])


async def test_remove_reverb_crossfades_windows_and_splits_back_into_pieces(monkeypatch):
    from pairvoice import curate

    # 区切りが1つ、窓の継ぎ目がいくつもあり、最後の窓は端数。どこも欠けず元と同じレベルに戻る。
    monkeypatch.setattr(fold, "DEREVERB_SPAN_SECONDS", 12.0)
    monkeypatch.setattr(fold, "DEREVERB_CONTEXT_SECONDS", 2.0)
    pieces = [tone(9.1, amp=0.1), tone(4.2, amp=0.2), tone(2.1, amp=0.1)]
    chunks, stages = [], []

    async def dereverb(samples):
        chunks.append(len(samples))
        return samples

    async def offload(fn, *args):
        return fn(*args)

    dry = await fold.remove_reverb(
        list(pieces), dereverb, offload, lambda stage, fraction: stages.append((stage, fraction))
    )

    assert set(chunks) == {curate.DEREVERB_CHUNK}
    assert [len(d) for d in dry] == [len(p) for p in pieces]
    for d, p in zip(dry, pieces, strict=True):
        np.testing.assert_allclose(d, p, atol=1e-3)
    fractions = [fraction for _, fraction in stages]
    assert {stage for stage, _ in stages} == {"dereverb"}
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0


async def test_speech_blocks_level_the_voice_like_a_reference():
    from pairvoice.postprocess import TARGET_RMS_DB, voiced_rms_db
    from tests.test_curate import material, offload, run_analyze

    # 素材のままの大きさ（-9 dB ほど）ではなく、ふだんの参照音声の大きさにそろえて符号化する
    analysis = await run_analyze(material([(1, 12, 0.5, 220), (13, 30, 0.5, 220)]))

    async def enhance(samples):
        return samples

    blocks = fold.speech_blocks(
        await fold.clean_pieces(analysis, enhance, offload, lambda *_: None)
    )

    assert len(blocks) == 1
    assert voiced_rms_db(blocks[0], RATE) == pytest.approx(TARGET_RMS_DB, abs=1.0)


async def test_fold_diarizes_raw_audio_before_cleaning(tmp_path, monkeypatch):
    from itertools import groupby

    from pairvoice import curate
    from tests.test_curate import FakeCurate, material, offload

    source = material([(1, 12, 0.5, 220), (13, 30, 0.5, 220)])

    def decode(files, directory):
        assert directory == tmp_path / "raw"
        return source

    monkeypatch.setattr(curate, "decode", decode)
    stages = []

    async def diarize(chunk):
        np.testing.assert_array_equal(chunk, source.audio16)
        return FakeCurate().diarize(chunk, None)[0]

    async def enhance(samples):
        assert stages[-1] == "clean"
        return samples

    async def dereverb(samples):
        assert stages[-1] == "dereverb"
        return samples

    async def encode(samples):
        assert stages[-1] == "encode"
        return np.ones((4, 8), np.float32)

    result = await fold.fold(
        [],
        tmp_path,
        enhance=enhance,
        diarize=diarize,
        dereverb=dereverb,
        encode=encode,
        model="test",
        offload=offload,
        progress=lambda stage, _: stages.append(stage),
    )
    assert [stage for stage, _ in groupby(stages)] == [
        "decode",
        "diarize",
        "clean",
        "dereverb",
        "encode",
        "summarize",
    ]
    assert result.report["blocks"] == 1


async def test_stopping_while_saving_lets_the_save_finish():
    import asyncio

    jobs = fold.FoldJobs()
    saving, release, saved = asyncio.Event(), asyncio.Event(), []

    async def work(progress):
        progress("save", 0.0)
        saving.set()
        await release.wait()
        saved.append(True)
        return {"id": "p"}, {}

    job = jobs.start(work, lambda error: ("internal_error", None))
    await saving.wait()
    stopping = asyncio.create_task(jobs.stop(job.job_id))
    await asyncio.sleep(0)
    release.set()
    await stopping

    # 保存は打ち切らず終えさせ、記録だけ消す
    assert saved == [True]
    assert job.status == "done"
    with pytest.raises(fold.FoldJobNotFound):
        jobs.get(job.job_id)
