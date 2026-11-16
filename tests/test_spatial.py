import wave

import numpy as np
import pytest

from pairvoice import spatial
from pairvoice.wav import write_mono

RATE = 24000


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return 0.5 * np.sin(2 * np.pi * 220 * t)


def channel_db(stereo: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.sqrt((stereo**2).mean(axis=0)))


def test_left_ear_is_louder_on_the_left():
    x = tone(1.0)
    n = len(x)

    left = spatial.render(x, RATE, np.full(n, -80.0), np.full(n, 1.0))
    right = spatial.render(x, RATE, np.full(n, 80.0), np.full(n, 1.0))

    assert channel_db(left)[0] - channel_db(left)[1] > 10
    assert channel_db(right)[1] - channel_db(right)[0] > 10
    assert np.abs(left).max() <= spatial.PEAK + 1e-9


def test_path_starts_and_ends_at_an_ear_and_stays_smooth():
    for seed in range(20):
        azimuth, near = spatial.path(RATE * 6, RATE, np.random.default_rng(seed))

        assert abs(azimuth[0]) >= 55
        assert abs(azimuth[-1]) >= 55
        assert np.all(np.abs(azimuth) <= 90)
        assert np.all((near >= 0.5) & (near <= 1.0))
        # 1サンプルで飛ばない（つなぎ目でプツッと鳴らない）
        assert np.abs(np.diff(azimuth)).max() < 0.05


def test_paths_differ_between_draws():
    a, _ = spatial.path(RATE * 6, RATE, np.random.default_rng(1))
    b, _ = spatial.path(RATE * 6, RATE, np.random.default_rng(2))

    assert not np.allclose(a, b)


def test_spatialize_writes_a_stereo_wav_next_to_the_source(tmp_path):
    source = tmp_path / "a.wav"
    write_mono(source, tone(2.0), RATE)

    target = spatial.spatialize(
        source, lambda x, rate: spatial.orbit(x, rate, np.random.default_rng(0))
    )

    assert target == tmp_path / "a.spatial.wav"
    with wave.open(str(target)) as reader:
        assert reader.getnchannels() == 2
        assert reader.getframerate() == RATE
        assert reader.getnframes() == RATE * 2


def test_outside_keeps_the_voice_centered_with_an_uncorrelated_room():
    # 純音だと反射と打ち消し合って片耳だけ小さくなるので、声に近い広い帯域の音で測る
    x = 0.3 * np.random.default_rng(0).standard_normal(RATE)

    stereo = spatial.outside(x, RATE)

    assert stereo.shape == (len(x) + len(spatial.room(RATE)) - 1, 2)
    assert np.abs(stereo).max() == pytest.approx(spatial.PEAK)
    db = channel_db(stereo)
    assert abs(db[1] - db[0]) < 1.5
    # 響きが左右で無相関なので、左右の耳に届く音が同じにならない（同じだと頭の中で鳴る）
    tail = spatial.room(RATE)[int(spatial.TAIL_START_MS / 1000 * RATE) :]
    assert abs(np.corrcoef(tail[:, 0], tail[:, 1])[0, 1]) < 0.2


def test_room_is_the_same_every_time():
    spatial.room.cache_clear()
    first = spatial.room(RATE).copy()
    spatial.room.cache_clear()

    assert np.array_equal(first, spatial.room(RATE))


def test_every_configurable_mode_has_a_placement():
    import typing

    from pairvoice.config import Spatial

    assert set(spatial.PLACEMENTS) | {"off"} == set(typing.get_args(Spatial))


# --- 合成しながら鳴らす ---


def test_outside_stream_adds_the_same_room_as_the_whole_at_once():
    from scipy.signal import fftconvolve

    rate = 24000
    rng = np.random.default_rng(0)
    # 山が丸められない大きさにして、響きだけを比べる
    samples = (0.05 * rng.standard_normal(rate)).astype(np.float32)
    whole = fftconvolve(samples.astype(np.float64)[:, None], spatial.room(rate), axes=0)
    whole *= 10 ** (spatial.OUTSIDE_STREAM_DB / 20)

    stream = spatial.OutsideStream(rate)
    # 響きの長さ（0.118 秒）より短い波形も混ぜる
    pieces = [stream.push(piece) for piece in np.split(samples, [100, 1300, 1400, 9000])]
    pieces.append(stream.flush())

    assert [len(piece) for piece in pieces[:-1]] == [100, 1200, 100, 7600, rate - 9000]
    assert all(piece.shape[1] == 2 and piece.dtype == np.float32 for piece in pieces)
    np.testing.assert_allclose(np.concatenate(pieces), whole, atol=1e-6)


def test_soft_clip_leaves_everything_below_the_knee_untouched():
    x = np.linspace(-2.0, 2.0, 801)

    y = spatial.soft_clip(x)

    quiet = np.abs(x) <= spatial.SOFT_KNEE
    np.testing.assert_array_equal(y[quiet], x[quiet])
    assert np.all(np.abs(y) < 1.0)
    # 大きさの順は変えない（丸めるだけで、折り返さない）
    assert np.all(np.diff(y) > 0)


def test_soft_clip_returns_quiet_audio_as_it_is():
    x = np.linspace(-0.5, 0.5, 11, dtype=np.float32)

    assert spatial.soft_clip(x) is x


def test_only_placements_that_work_on_arriving_audio_can_stream():
    # 耳元で動かすのは、通る道を全体の長さから決めるので、合成を待ってから鳴らす
    assert set(spatial.STREAM_SHAPERS) == {"outside", "off"}
    assert spatial.STREAM_SHAPERS["outside"] is spatial.OutsideStream
    # そのまま鳴らすときは、何も掛けない
    assert spatial.STREAM_SHAPERS["off"] is None
