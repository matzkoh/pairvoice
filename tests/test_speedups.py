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


def _streamed(model, dac, encoded):
    pieces = []
    with speedups.streaming(model, lambda wave, remaining: pieces.append((wave, remaining))):
        whole = np.asarray(dac.decode(encoded, chunk_size=50))[0, :, 0]
    return pieces, whole


def test_streaming_hands_over_the_same_wave_chunk_by_chunk(installed):
    model, dac = installed
    rng = np.random.default_rng(3)
    encoded = mx.array(rng.normal(size=(1, 6, 130)).astype(np.float32))
    speedups.install(model)

    pieces, whole = _streamed(model, dac, encoded)

    # 130 フレームは 50 フレームずつ 4 フレーム重ねて 3 チャンク。最後以外は重ねる所を次まで持ち越す
    hop = dac.hop_length
    assert [len(wave) for wave, _ in pieces] == [46 * hop, 46 * hop, 38 * hop]
    assert [remaining for _, remaining in pieces] == [84 * hop, 38 * hop, 0]
    np.testing.assert_array_equal(np.concatenate([wave for wave, _ in pieces]), whole)


def test_streaming_stops_where_generate_cuts_the_silence(installed):
    model, dac = installed
    latent = _latent_with_silent_tail(130, 70)
    encoded = mx.transpose(latent[None], (0, 2, 1))
    speedups.install(model)
    kept = ORIGINAL_SILENCE_POINT(latent) * dac.hop_length

    pieces, whole = _streamed(model, dac, encoded)

    streamed = np.concatenate([wave for wave, _ in pieces])
    assert len(streamed) == kept
    assert pieces[-1][1] == 0
    np.testing.assert_array_equal(streamed, whole[:kept])


def test_streaming_a_single_chunk_hands_over_everything_at_once(installed):
    model, dac = installed
    rng = np.random.default_rng(4)
    encoded = mx.array(rng.normal(size=(1, 6, 30)).astype(np.float32))
    speedups.install(model)

    pieces, whole = _streamed(model, dac, encoded)

    assert [remaining for _, remaining in pieces] == [0]
    np.testing.assert_array_equal(pieces[0][0], whole)


def test_decode_does_not_hand_over_outside_streaming(installed):
    model, dac = installed
    rng = np.random.default_rng(5)
    encoded = mx.array(rng.normal(size=(1, 6, 130)).astype(np.float32))
    speedups.install(model)
    pieces, _ = _streamed(model, dac, encoded)
    seen = len(pieces)

    dac.decode(encoded, chunk_size=50)

    assert len(pieces) == seen


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


@pytest.mark.parametrize(
    ("self_keys", "used", "keep"),
    # 本物のテキストのキーを含む最後のブロック（32 個ずつ）より後ろを、32 の倍数だけ除く
    [(179, 21, 64), (37, 23, 32), (0, 32, 32), (0, 33, 64), (0, 0, 0), (37, 230, None)],
)
def test_text_padding_is_trimmed_after_the_last_used_key_block(self_keys, used, keep):
    mask = np.zeros((3, 256), bool)
    mask[0, :used] = True  # 他の行はテキストを外した CFG の行のように全部マスク

    kept = speedups._kept_text_keys(mx.array(mask), self_keys)

    assert kept == keep


def _tiny_dit():
    from mlx_audio.tts.models.irodori_tts.model import JointAttention

    mx.random.seed(0)
    attention = JointAttention(dim=128, heads=2, text_ctx_dim=8, speaker_ctx_dim=8, norm_eps=1e-6)
    mx.eval(attention.parameters())
    return type("DiT", (), {"blocks": [type("Block", (), {"attention": attention})()]})()


def test_trimming_text_padding_is_checked_against_the_real_attention(monkeypatch):
    dit = _tiny_dit()
    assert speedups._text_trim_is_exact(dit)

    # 最後の本物のキーと話者のキーを同じブロックに入れる除き方は値を変えるので、確かめが外す
    def across_the_last_block(text_mask, self_keys):
        used = int(np.flatnonzero(np.asarray(text_mask).any(axis=0))[-1]) + 1
        return 256 - (256 - used) // 32 * 32

    monkeypatch.setattr(speedups, "_kept_text_keys", across_the_last_block)
    assert not speedups._text_trim_is_exact(dit)


def test_encode_matches_before_and_after_speaker_speedups(installed):
    model, dac = installed
    audio = mx.array(np.random.default_rng(3).normal(size=(1, 1201, 1)).astype(np.float32))
    before = np.asarray(dac.encode(audio))

    speedups.install(model)
    np.testing.assert_allclose(dac.encode(audio), before, atol=1e-5)


def test_fused_attention_preserves_roformer_heads_and_rotations():
    from mlx_audio.sts.models.mel_roformer.model import RoFormerAttention

    from pairvoice.curate_speedups import roformer_attention

    mx.random.seed(7)
    layer = RoFormerAttention(dim=64, heads=4, dim_head=16)
    x = mx.random.normal((2, 117, 64))
    np.testing.assert_allclose(roformer_attention(layer)(x), layer(x), atol=1e-5)
