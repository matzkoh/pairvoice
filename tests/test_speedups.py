import mlx.core as mx
import numpy as np
import pytest
from mlx_audio.codec.models.dacvae.codec import DACVAE, DACVAEConfig
from mlx_audio.tts.models.irodori_tts import irodori_tts

from pairvoice import speedups

ORIGINAL_SILENCE_POINT = irodori_tts._find_silence_point


class FakeDiT:
    def __init__(self):
        self.calls = []
        self.cfg = type("Cfg", (), {"caption_dim_resolved": 3})()

    def encode_conditions_full(self, **kwargs):
        self.calls.append(kwargs)
        caption = kwargs.get("caption_input_ids")
        return (
            mx.ones((1, 2, 3)),
            mx.ones((1, 2), dtype=mx.bool_),
            None,
            None,
            None if caption is None else mx.ones((1, 4, 3), dtype=mx.float16),
            kwargs.get("caption_mask"),
        )


class FakeModel:
    def __init__(self, dac):
        self.model = FakeDiT()
        self.dacvae = dac


def tiny_dac():
    # 本物と同じ組み立てで、チャンネルと倍率だけ小さくしたもの（重みは乱数）
    mx.random.seed(0)
    dac = DACVAE(
        DACVAEConfig(
            encoder_dim=8,
            encoder_rates=[4, 2],
            latent_dim=48,
            decoder_dim=48,
            decoder_rates=[4, 2],
            codebook_dim=6,
        )
    )
    mx.eval(dac.parameters())
    return dac


@pytest.fixture
def installed(monkeypatch):
    # install は mlx-audio のモジュールの関数を差し替えるので、テストの後に戻す
    monkeypatch.setattr(irodori_tts, "_find_silence_point", ORIGINAL_SILENCE_POINT)
    dac = tiny_dac()
    model = FakeModel(dac)
    return model, dac


def _latent_with_silent_tail(frames, voiced):
    rng = np.random.default_rng(1)
    latent = np.zeros((frames, 6), np.float32)
    latent[:voiced] = rng.normal(size=(voiced, 6))
    return mx.array(latent)


@pytest.mark.parametrize("voiced", [0, 1, 37, 90, 120])
def test_find_silence_point_matches_mlx_audio(voiced):
    latent = _latent_with_silent_tail(120, voiced)

    assert speedups.find_silence_point(latent) == ORIGINAL_SILENCE_POINT(latent)


def test_find_silence_point_checks_windows_near_the_threshold_with_the_original_formula():
    # 標準偏差がしきい値すれすれの窓は、numpy の見積もりで決めずに元の式で確かめる
    latent = np.zeros((60, 4), np.float32)
    latent[:30] = 0.05
    latent[:30:2] = -0.05
    latent = mx.array(latent)

    assert speedups.find_silence_point(latent) == ORIGINAL_SILENCE_POINT(latent)


def test_silence_upper_bound_never_cuts_before_the_silence_point():
    for voiced in (5, 40, 100):
        latent = _latent_with_silent_tail(120, voiced)
        bound = speedups.silence_upper_bound(latent)
        assert bound is not None
        assert bound >= ORIGINAL_SILENCE_POINT(latent)
    # 先頭から無音なら generate() は切り捨てないので、全部を解かせる
    assert speedups.silence_upper_bound(_latent_with_silent_tail(120, 0)) is None


def test_decode_matches_the_original_where_generate_keeps_it(installed):
    model, dac = installed
    frames = 130
    latent = _latent_with_silent_tail(frames, 70)
    encoded = mx.transpose(latent[None], (0, 2, 1))
    before = np.asarray(dac.decode(encoded, chunk_size=50))
    kept = ORIGINAL_SILENCE_POINT(latent) * dac.hop_length

    assert speedups.install(model)
    after = np.asarray(dac.decode(encoded, chunk_size=50))

    # 転置畳み込みと snake は足し合わせの順だけが違う
    np.testing.assert_allclose(after[:, :kept], before[:, :kept], atol=1e-5)


def test_decode_matches_the_original_without_silence(installed):
    model, dac = installed
    rng = np.random.default_rng(2)
    encoded = mx.array(rng.normal(size=(1, 6, 130)).astype(np.float32))
    before = np.asarray(dac.decode(encoded, chunk_size=50))

    speedups.install(model)
    after = np.asarray(dac.decode(encoded, chunk_size=50))

    assert after.shape == before.shape
    np.testing.assert_allclose(after, before, atol=1e-5)


def test_encode_conditions_full_runs_once_per_generation(installed):
    model, _ = installed
    dit = model.model
    speedups.install(model)
    ids, mask = mx.array([[1, 2]]), mx.array([[True, True]])
    caption, caption_mask = mx.array([[1]]), mx.array([[True]])

    first = dit.encode_conditions_full(
        text_input_ids=ids, text_mask=mask, caption_input_ids=caption, caption_mask=caption_mask
    )
    again = dit.encode_conditions_full(
        text_input_ids=ids, text_mask=mask, caption_input_ids=caption, caption_mask=caption_mask
    )
    other = dit.encode_conditions_full(
        text_input_ids=mx.array([[1, 2]]),
        text_mask=mask,
        caption_input_ids=caption,
        caption_mask=caption_mask,
    )

    # 同じ配列で呼ばれたら（継続時間の予測とサンプラー）2回目は計算しない
    assert again is first
    assert other is not first
    assert len(dit.calls) == 2


def test_empty_caption_becomes_a_zero_length_sequence_of_the_same_type(installed):
    model, _ = installed
    dit = model.model
    speedups.install(model)
    ids, mask = mx.array([[1, 2]]), mx.array([[True, True]])

    outs = [
        dit.encode_conditions_full(
            text_input_ids=ids,
            text_mask=mask,
            caption_input_ids=mx.array([[1, 0]]),
            caption_mask=mx.array([[False, False]]),
        )
        for _ in range(2)
    ]

    for out in outs:
        assert out[4].shape == (1, 0, 3)
        # 型は元の caption の列と同じ（違うと、つないだキーの型が昇格して計算が変わる）
        assert out[4].dtype == mx.float16
        assert out[5].shape == (1, 0)
    # 型を知るための最初の1回だけ caption を符号化する
    assert [call["caption_input_ids"] is None for call in dit.calls] == [False, True]
