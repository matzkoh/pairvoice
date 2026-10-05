import numpy as np

from pairvoice.postprocess import normalize, trim

SR = 1000


def _signal(*parts):
    """(秒, 振幅) の並びから、音（正弦波）と無音をつないだ波形を作る。"""
    chunks = []
    for seconds, amplitude in parts:
        t = np.arange(int(seconds * SR)) / SR
        chunks.append(amplitude * np.sin(2 * np.pi * 50 * t))
    return np.concatenate(chunks).astype(np.float32)


def test_drops_short_burst_after_a_gap():
    samples = _signal((2.0, 0.5), (0.3, 0.0), (0.2, 0.5))

    trimmed = trim(samples, SR)

    # 本文の終わり + 余韻まで
    assert len(trimmed) == int(2.1 * SR)


def test_drops_up_to_two_bursts():
    samples = _signal((2.0, 0.5), (0.3, 0.0), (0.2, 0.4), (0.3, 0.0), (0.1, 0.5))

    assert len(trim(samples, SR)) == int(2.1 * SR)


def test_keeps_long_final_phrase():
    samples = _signal((2.0, 0.5), (0.3, 0.0), (0.8, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_keeps_ending_without_a_gap():
    # 間が短ければ本文の語尾とみなす
    samples = _signal((2.0, 0.5), (0.05, 0.0), (0.2, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_drops_short_click_before_speech():
    samples = _signal((0.05, 0.3), (0.3, 0.0), (2.0, 0.5))

    trimmed = trim(samples, SR)

    # 頭の無音は 0.05 秒だけ残す
    assert len(trimmed) == int(2.05 * SR)


def test_keeps_short_first_word_before_a_pause():
    # 「次に、」のような語頭は頭のゴミより長い
    samples = _signal((0.3, 0.5), (0.3, 0.0), (2.0, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_trims_trailing_silence_and_fades_out():
    samples = _signal((1.0, 0.5), (1.0, 0.0))

    trimmed = trim(samples, SR)

    assert len(trimmed) == int(1.1 * SR)
    assert trimmed[-1] == 0.0


def test_single_burst_is_kept():
    samples = _signal((0.3, 0.5))

    assert len(trim(samples, SR)) == len(samples)


def test_empty_input():
    assert len(trim(np.zeros(0, dtype=np.float32), SR)) == 0


def _voiced_rms_db(samples):
    voiced = samples[np.abs(samples) > 0]
    return 20 * np.log10(np.sqrt(np.mean(voiced**2)))


def test_normalize_brings_quiet_voice_to_target():
    samples = _signal((1.0, 0.1))

    normalized = normalize(samples, SR)

    assert abs(_voiced_rms_db(normalized) - (-16.0)) < 0.5


def test_normalize_keeps_peak_below_ceiling():
    # 声の RMS は低いが一瞬だけ大きい音があると、RMS ではなくピークで止まる
    samples = _signal((1.0, 0.05))
    samples[100] = 0.99

    normalized = normalize(samples, SR)

    assert np.abs(normalized).max() <= 10 ** (-1.0 / 20) + 1e-6


def test_normalize_caps_gain_for_near_silence():
    samples = _signal((1.0, 0.001))

    gain = np.abs(normalize(samples, SR)).max() / np.abs(samples).max()

    assert abs(20 * np.log10(gain) - 12.0) < 0.01


def test_normalize_leaves_silence_alone():
    samples = np.zeros(SR, dtype=np.float32)

    assert np.array_equal(normalize(samples, SR), samples)
