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


async def test_clean_stitches_spans_back_to_the_full_length(tmp_path, monkeypatch):
    import wave

    from pairvoice.curate import Material, Source

    monkeypatch.setattr(fold, "SPAN_SECONDS", 2.0)
    monkeypatch.setattr(fold, "SPAN_CONTEXT_SECONDS", 0.5)
    monkeypatch.setattr(fold, "ENHANCE_SECONDS", 0.7)
    x = tone(5.3, amp=0.3)
    material = Material(np.zeros(1), np.stack([x, x * 0.5], axis=1), [Source("a", 0.0, 5.3)])

    async def same(samples):
        return samples

    async def offload(fn, *args):
        return fn(*args)

    [(name, path)] = await fold.clean(material, tmp_path, same, None, offload, lambda *_: None)

    with wave.open(str(path)) as reader:
        out = np.frombuffer(reader.readframes(reader.getnframes()), "<i2") / 32767.0
    # 区切りの前後の文脈は捨てて、元と同じ長さ・同じ音に戻る（大きい方のチャンネルを使う）
    assert name == "a"
    np.testing.assert_allclose(out, x, atol=1e-4)


async def test_fold_crossfades_dereverb_windows_and_keeps_the_tail():
    from pairvoice import curate

    # 継ぎ目が2つあり、最後の窓は端数。どこも欠けず元と同じレベルに戻る。
    x = tone(15.4, amp=0.1)
    chunks = []

    async def enhance(samples):
        return samples

    async def dereverb(samples):
        chunks.append(len(samples))
        return samples

    async def offload(fn, *args):
        return fn(*args)

    y = await fold._clean_span(x, enhance, dereverb, offload)
    assert chunks == [curate.DEREVERB_CHUNK] * 3
    np.testing.assert_allclose(y, x, atol=1e-3)


async def test_speech_blocks_level_the_voice_like_a_reference():
    from pairvoice.postprocess import TARGET_RMS_DB, voiced_rms_db
    from tests.test_curate import material, run_analyze

    # 素材のままの大きさ（-9 dB ほど）ではなく、ふだんの参照音声の大きさにそろえて符号化する
    analysis = await run_analyze(material([(1, 12, 0.5, 220), (13, 30, 0.5, 220)]))

    blocks = fold.speech_blocks(analysis)

    assert len(blocks) == 1
    assert voiced_rms_db(blocks[0], RATE) == pytest.approx(TARGET_RMS_DB, abs=1.0)


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
