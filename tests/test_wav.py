import numpy as np
import pytest

from pairvoice.wav import WavFormatError, band_limited, pcm_wav, read_mono

RATE = 48000


def voice(slope_db_per_octave: float, cutoff: float | None = None, seconds: float = 2.0):
    """200Hz の倍音と弱い息の雑音。倍音は1オクターブごとに slope だけ弱め、cutoff より上は消す。"""
    t = np.arange(int(RATE * seconds)) / RATE
    x = sum(
        10 ** (-slope_db_per_octave * np.log2(k) / 20) * np.sin(2 * np.pi * 200 * k * t)
        for k in range(1, 100)
    )
    x = x + 0.001 * np.random.default_rng(0).standard_normal(len(t))
    if cutoff is not None:
        spectrum = np.fft.rfft(x)
        spectrum[np.fft.rfftfreq(len(x), 1 / RATE) > cutoff] = 0
        x = np.fft.irfft(spectrum, len(x))
    return 0.5 * x / np.abs(x).max()


def test_full_band_voice_is_not_band_limited():
    assert not band_limited(voice(6), RATE)


def test_dark_voice_is_not_band_limited():
    # 暗い声でも上の帯域は残る（こもった音と取り違えない）
    assert not band_limited(voice(12), RATE)


@pytest.mark.parametrize("cutoff", [12000, 10000])
def test_voice_cut_above_12khz_is_band_limited(cutoff):
    assert band_limited(voice(6, cutoff), RATE)


def test_low_sample_rate_is_band_limited():
    assert band_limited(np.zeros(24000), 24000)


def test_silence_is_not_band_limited():
    assert not band_limited(np.zeros(RATE), RATE)
    assert not band_limited(np.zeros(10), RATE)


def test_read_mono_averages_channels(tmp_path):
    path = tmp_path / "stereo.wav"
    path.write_bytes(pcm_wav(np.array([[0.5, 0.0], [-0.5, -0.25]]), RATE))

    samples, rate = read_mono(path)

    assert rate == RATE
    assert samples == pytest.approx([0.25, -0.375], abs=1e-4)


def test_read_mono_rejects_non_wav(tmp_path):
    path = tmp_path / "broken.wav"
    path.write_bytes(b"not a wav")

    with pytest.raises(WavFormatError):
        read_mono(path)
