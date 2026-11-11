import pytest

from pairvoice.voice_eval import Sample, cer, normalize, score


def test_normalize_ignores_width_kana_and_punctuation():
    assert normalize("テスト、全部 通った！ＯＫ") == normalize("てすと全部通った OK")


@pytest.mark.parametrize(
    ("reference", "hypothesis", "expected"),
    [
        ("全部通った。", "全部通った", 0.0),
        ("全部通った", "全部通", 0.4),
        ("全部通った", "全部全部通った", 0.4),
        ("", "", 0.0),
    ],
)
def test_cer_counts_edits_against_the_script(reference, hypothesis, expected):
    assert cer(reference, hypothesis) == pytest.approx(expected)


def sample(candidate, text, duration, transcript, seed=1):
    return Sample(
        candidate, text, seed, f"generations/{candidate}-{seed}.wav", duration, transcript
    )


def test_score_ranks_broken_takes_before_misreadings():
    samples = [
        # 0: 読みは完璧だが、1本が長く伸びた（無音が居座った）
        sample(0, "あいうえお", 2.0, "あいうえお"),
        sample(0, "あいうえお", 4.0, "あいうえお", seed=2),
        # 1: 1文字読み違えたが、長さはそろっている
        sample(1, "あいうえお", 2.0, "あいうえか"),
        sample(1, "あいうえお", 2.1, "あいうえお", seed=2),
    ]

    result = score(samples, 2)

    assert result["ranking"] == [1, 0]
    first, second = result["candidates"]
    assert first["broken"] == 1
    assert [s["broken"] for s in first["samples"]] == [False, True]
    assert second["cer"] == pytest.approx(0.1)
    assert second["worst_cer"] == pytest.approx(0.2)
