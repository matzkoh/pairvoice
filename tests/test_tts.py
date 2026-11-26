import dataclasses
import types
import wave

import numpy as np
import pytest

from pairvoice import speedups
from pairvoice.config import ProfileConfig, SamplerConfig, TTSConfig
from pairvoice.profiles import (
    MixVoice,
    ProfileNotFound,
    ProfileRejected,
    ProfileStore,
    SpeakerTokens,
)
from pairvoice.tts import (
    ANCHOR_TEXT,
    LEAD_TEXT,
    NON_FINAL_DURATION_SCALE,
    AudioNotFound,
    MlxAudioBackend,
    UnmuffleUnsupported,
)
from pairvoice.unmuffle import BOOM_RATIO


class FakeResult:
    def __init__(self, samples, sample_rate=48000, peak=1234):
        self.audio = samples
        self.sample_rate = sample_rate
        self.peak_memory_usage = peak


class FakeModel:
    """前置き（LEAD_TEXT）が付いた文には、本物と同じく前置きの声と間を足して返す。

    calls の text は前置きを除いた本文で記録し、前置きを渡したかは lead に残す。
    speaks_lead=False は、前置きの後に間を置かないモデル（切れずに合成し直す）を真似る。
    """

    def __init__(self, result, speaks_lead=True):
        self.result = result
        self.speaks_lead = speaks_lead
        self.calls = []

    def generate(self, text, **kwargs):
        lead = text.startswith(LEAD_TEXT)
        self.calls.append({"text": text.removeprefix(LEAD_TEXT), "lead": lead, **kwargs})
        if not lead:
            return iter([self.result])
        sample_rate = getattr(self.result, "sample_rate", 48000)
        body = np.asarray(self.result.audio, dtype=np.float32)
        # 本物の本文は声で始まる。無音の本文（多くのテストの既定）では切れ目が見えないので声を足す
        if not np.any(body):
            body = np.concatenate([_tone(0.05, sample_rate), body])
        gap = np.zeros(int(0.3 * sample_rate) if self.speaks_lead else 0, dtype=np.float32)
        audio = np.concatenate([_tone(0.3, sample_rate), gap, body])
        return iter([FakeResult(audio, sample_rate, self.result.peak_memory_usage)])


def _tone(seconds, sample_rate=48000, amplitude=0.3):
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return (amplitude * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


# SamplerConfig が None 以外を既定に持つ項目
SPEED_DEFAULTS = {"num_steps": 10, "t_schedule_mode": "sway", "sway_coeff": -1.0, "cfg_min_t": 0.75}


@pytest.fixture
def config(tmp_path):
    ref = tmp_path / "profiles" / "reference.wav"
    ref.parent.mkdir(parents=True)
    ref.write_bytes(b"\x00" * 16)  # preflight はサイズしか見ないので wav として妥当である必要はない
    return TTSConfig(
        model="fake/tts",
        output_dir=tmp_path / "generations",
        sampler=SamplerConfig(cfg_guidance_mode="alternating", cfg_scale_speaker=2.5),
        profile=ProfileConfig(name="テスト", ref_audio=ref, caption="やわらかい声。"),
    )


@pytest.fixture
def no_ref_config(config):
    return TTSConfig(
        model=config.model,
        output_dir=config.output_dir,
        sampler=config.sampler,
        profile=ProfileConfig(name="テスト", ref_audio=None, caption="やわらかい声。"),
    )


def make_backend(config, model, tmp_path, monkeypatch):
    backend = MlxAudioBackend(config, data_dir=tmp_path)
    monkeypatch.setattr(backend, "_load_model", lambda: model)
    backend.load()
    return backend


def test_preflight_reports_missing_reference_audio(config, tmp_path):
    config.profile.ref_audio.unlink()
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.preflight() == "profile_missing"


def test_preflight_reports_zero_byte_reference_audio(config, tmp_path):
    config.profile.ref_audio.write_bytes(b"")
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.preflight() == "profile_missing"


def test_preflight_passes_when_reference_audio_exists(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.preflight() is None


def test_speak_writes_48khz_wav_and_returns_relative_path(config, tmp_path, monkeypatch):
    samples = np.linspace(-0.5, 0.5, 48000, dtype=np.float32)
    model = FakeModel(FakeResult(samples))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    result = backend.speak("やった、テスト全部通ったよ。")

    assert result.path.exists()
    assert result.relative_path == f"generations/{result.path.name}"
    # 前置きを切った後、本文の手前に HEAD_PAD_SECONDS（0.05 秒）の間が残る
    assert result.duration == pytest.approx(1.05, abs=0.01)
    assert result.peak_memory_gb == 1234

    with wave.open(str(result.path)) as wav:
        assert wav.getframerate() == 48000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 50400


def test_speak_passes_ref_audio_caption_and_sampler_only(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    call = model.calls[0]
    assert call["text"] == "テスト"
    # 設定の参照音声はプロファイルに取り込んだ複製で読む
    profile = ProfileStore(tmp_path / "profiles").active()
    assert profile is not None
    assert call["ref_audio"] == str(profile.reference)
    assert call["caption"] == "やわらかい声。"
    # sampler は項目を増やしても配線漏れが起きないよう丸ごと渡す。None は
    # 「モデル既定に任せる」なのでキーごと落ちる。余分な引数も渡らないこと
    assert {
        k: v for k, v in call.items() if k not in ("text", "lead", "ref_audio", "caption")
    } == backend.resolve_sampler()
    assert "ref_text" not in call  # Irodori-TTS に参照テキストのパラメータは無い
    assert "stream" not in call


def test_speak_reads_text_through_dict(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("PR が通った")
    (tmp_path / "dict.tsv").write_text("PR\tピーアール\t\n", encoding="utf-8")
    # 合成のたびに読むので、studio で保存した辞書が再起動なしに効く
    backend.speak("PR が通った")

    assert [call["text"] for call in model.calls] == ["PR が通った", "ピーアール が通った"]


def test_preflight_passes_without_reference_audio(no_ref_config, tmp_path):
    backend = MlxAudioBackend(no_ref_config, data_dir=tmp_path)

    assert backend.preflight() is None


def test_bootstrap_designs_anchor_voice_then_clones_it(no_ref_config, tmp_path, monkeypatch):
    # VoiceDesign は文ごとに別人の声になるので、caption から一度だけ声を作り、
    # 以降はそれを参照音声にして揃える
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    anchor, spoken = model.calls
    assert anchor["text"] == ANCHOR_TEXT
    assert anchor["ref_audio"] is None
    assert anchor["caption"] == "やわらかい声。"
    assert anchor["rng_seed"] == 0
    profile = ProfileStore(tmp_path / "profiles").active()
    assert profile is not None
    assert profile.source == "auto"
    assert profile.caption == "やわらかい声。"
    assert spoken["text"] == "テスト"
    assert spoken["ref_audio"] == str(profile.reference)


def test_bootstrap_runs_only_once(no_ref_config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("一回目")
    backend.speak("二回目")

    assert [c["text"] for c in model.calls] == [ANCHOR_TEXT, "一回目", "二回目"]


def test_bootstrap_imports_configured_reference_as_copy(config, tmp_path, monkeypatch):
    (tmp_path / "caption.txt").write_text("採用済みの声。", encoding="utf-8")
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    profile = ProfileStore(tmp_path / "profiles").active()
    assert profile is not None
    assert profile.source == "import"
    assert profile.name == "テスト"
    # 旧来の採用済み caption を引き継ぐ
    assert profile.caption == "採用済みの声。"
    assert profile.reference is not None
    assert profile.reference.read_bytes() == config.profile.ref_audio.read_bytes()
    assert profile.reference != config.profile.ref_audio
    assert len(model.calls) == 1


def test_speak_uses_active_profile_reference_and_caption(no_ref_config, tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles")
    profile = store.create(
        name="作った声",
        caption="作ったときの声。",
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    (call,) = model.calls
    assert call["ref_audio"] == str(profile.reference)
    assert call["caption"] == "作ったときの声。"


def test_speak_with_profile_id_uses_that_profile_instead_of_active(
    no_ref_config, tmp_path, monkeypatch
):
    store = ProfileStore(tmp_path / "profiles")
    other = store.create(
        name="試す声",
        caption="試すときの声。",
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )
    store.create(
        name="使用中の声",
        caption="使用中の声。",
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト", profile_id=other.id)

    (call,) = model.calls
    assert call["ref_audio"] == str(other.reference)
    assert call["caption"] == "試すときの声。"


def test_speak_with_unknown_profile_id_raises_without_bootstrap(
    no_ref_config, tmp_path, monkeypatch
):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    with pytest.raises(ProfileNotFound):
        backend.speak("テスト", profile_id="p-missing")

    assert model.calls == []
    assert ProfileStore(tmp_path / "profiles").active() is None


def test_design_mode_ignores_profile_and_skips_bootstrap(no_ref_config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("候補", caption="試す声。", design=True)

    (call,) = model.calls
    assert call["ref_audio"] is None
    assert call["caption"] == "試す声。"
    assert ProfileStore(tmp_path / "profiles").active() is None


def test_resolve_caption_prefers_profile_over_caption_file(config, tmp_path):
    (tmp_path / "caption.txt").write_text("旧来の声。", encoding="utf-8")
    ProfileStore(tmp_path / "profiles").create(
        name="a",
        caption="プロファイルの声。",
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.resolve_caption() == ("プロファイルの声。", "profile")
    assert backend.resolve_caption("試す声。") == ("試す声。", "request")


def test_preflight_passes_with_active_profile_even_if_configured_reference_is_gone(
    config, tmp_path
):
    ProfileStore(tmp_path / "profiles").create(
        name="a",
        caption="声。",
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )
    config.profile.ref_audio.unlink()
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.preflight() is None


def test_describe_profile(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)
    assert backend.describe_profile() is None

    profile = ProfileStore(tmp_path / "profiles").create(
        name="作った声",
        caption="声。",
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )

    assert backend.describe_profile() == {"id": profile.id, "name": "作った声", "source": "design"}


def test_speak_defaults_sample_rate_when_result_lacks_it(config, tmp_path, monkeypatch):
    result = FakeResult(np.zeros(24000, dtype=np.float32))
    del result.sample_rate
    model = FakeModel(result)
    backend = make_backend(config, model, tmp_path, monkeypatch)

    speech = backend.speak("テスト")

    with wave.open(str(speech.path)) as wav:
        assert wav.getframerate() == 48000


def test_load_creates_output_directory(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    assert config.output_dir.exists()
    assert backend.speak("テスト").path.parent == config.output_dir


def test_speak_recreates_output_directory_if_removed_after_load(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)
    config.output_dir.rmdir()
    assert not config.output_dir.exists()

    result = backend.speak("テスト")

    assert result.path.exists()
    assert result.path.parent == config.output_dir


def test_speak_without_load_raises(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    with pytest.raises(RuntimeError, match="not loaded"):
        backend.speak("テスト")


def test_speak_raises_when_model_yields_nothing(config, tmp_path, monkeypatch):
    class EmptyModel:
        def generate(self, text, **kwargs):
            return iter([])

    backend = make_backend(config, EmptyModel(), tmp_path, monkeypatch)

    with pytest.raises(RuntimeError, match="no audio"):
        backend.speak("テスト")


def test_unload_clears_references_and_cache(config, tmp_path, monkeypatch):
    cleared = []
    monkeypatch.setattr("pairvoice.tts.mx.clear_cache", lambda: cleared.append(True))
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.unload()

    assert cleared == [True]
    with pytest.raises(RuntimeError, match="not loaded"):
        backend.speak("テスト")


def test_resolve_caption_falls_back_to_config_default(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.resolve_caption() == ("やわらかい声。", "config")


def test_resolve_caption_prefers_caption_file(config, tmp_path):
    (tmp_path / "caption.txt").write_text("落ち着いた低い声。\n", encoding="utf-8")
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    # 末尾の改行は生成に持ち込まない
    assert backend.resolve_caption() == ("落ち着いた低い声。", "file")


def test_resolve_caption_ignores_blank_caption_file(config, tmp_path):
    # 空にしただけで既定値へ戻れないと、消しても直前の版が residual に効いて見える
    (tmp_path / "caption.txt").write_text("   \n", encoding="utf-8")
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.resolve_caption() == ("やわらかい声。", "config")


def test_resolve_caption_prefers_override_over_file(config, tmp_path):
    (tmp_path / "caption.txt").write_text("落ち着いた低い声。", encoding="utf-8")
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.resolve_caption("試している声。") == ("試している声。", "request")


def test_resolve_caption_keeps_blank_override_as_no_caption(config, tmp_path):
    # 空は「caption なしで読む」の指定。既定値へ落とすと、なしを試せない
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.resolve_caption("  ") == ("", "request")


def test_resolve_caption_keeps_blank_profile_caption(config, tmp_path):
    (tmp_path / "caption.txt").write_text("旧来の声。", encoding="utf-8")
    ProfileStore(tmp_path / "profiles").create(
        name="a",
        caption="",
        source="upload",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    # プロファイルができた後は caption.txt と設定を読まない（空でも）
    assert backend.resolve_caption() == ("", "profile")


def test_speak_uses_caption_file_when_present(config, tmp_path, monkeypatch):
    (tmp_path / "caption.txt").write_text("落ち着いた低い声。", encoding="utf-8")
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    assert model.calls[0]["caption"] == "落ち着いた低い声。"


def test_speak_override_does_not_touch_caption_file(config, tmp_path, monkeypatch):
    caption_file = tmp_path / "caption.txt"
    caption_file.write_text("採用済みの声。", encoding="utf-8")
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト", caption="試している声。")

    assert model.calls[0]["caption"] == "試している声。"
    # 試聴が採用済みの版を書き換えると、その最中のフックの読み上げが試行中の声で鳴る
    assert caption_file.read_text(encoding="utf-8") == "採用済みの声。"


def test_resolve_sampler_drops_none_so_model_defaults_apply(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    # fixture が指定した2項目と、pairvoice が速さのために既定で外す項目だけが残る。
    # 残りは渡さないことでモデル既定に委ねる
    assert backend.resolve_sampler() == {
        **SPEED_DEFAULTS,
        "cfg_guidance_mode": "alternating",
        "cfg_scale_speaker": 2.5,
    }


def test_resolve_sampler_overlays_override_on_config(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    resolved = backend.resolve_sampler({"num_steps": 60, "cfg_scale_speaker": 4.0})

    assert resolved == {
        **SPEED_DEFAULTS,
        "cfg_guidance_mode": "alternating",  # 上書きされていない項目は config のまま
        "cfg_scale_speaker": 4.0,
        "num_steps": 60,
    }


def test_speak_passes_overridden_sampler_to_generate(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト", sampler={"num_steps": 60, "rng_seed": 7})

    call = model.calls[0]
    assert call["num_steps"] == 60
    assert call["rng_seed"] == 7
    assert call["cfg_guidance_mode"] == "alternating"


def test_speak_without_sampler_keeps_config_values(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    call = model.calls[0]
    assert {k: v for k, v in call.items() if k not in ("text", "lead", "ref_audio", "caption")} == {
        **SPEED_DEFAULTS,
        "cfg_guidance_mode": "alternating",
        "cfg_scale_speaker": 2.5,
    }


def test_backend_exposes_anchor_text(config, tmp_path):
    assert MlxAudioBackend(config, data_dir=tmp_path).anchor_text == ANCHOR_TEXT


def test_speak_postprocesses_output_with_a_nan(config, tmp_path, monkeypatch):
    # NaN が1つでも混ざると trim / normalize が素通しになり、その位置の書き出しは
    # NaN の整数化（プラットフォーム依存で、フルスケールにもなる）に任される
    clean = 0.1 * np.sin(np.linspace(0, 200 * np.pi, 48000, dtype=np.float32))
    broken = clean.copy()
    broken[24000] = np.nan

    def written(samples):
        backend = make_backend(config, FakeModel(FakeResult(samples)), tmp_path, monkeypatch)
        with wave.open(str(backend.speak("テスト").path)) as wav:
            return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")

    expected, actual = written(clean), written(broken)
    assert actual.shape == expected.shape
    assert np.abs(actual.astype(np.int32)).max() == np.abs(expected.astype(np.int32)).max()


def test_preflight_reports_directory_as_reference_audio(config, tmp_path):
    config.profile.ref_audio.unlink()
    config.profile.ref_audio.mkdir()
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.preflight() == "profile_missing"


def test_resolve_caption_ignores_non_utf8_caption_file(config, tmp_path):
    (tmp_path / "caption.txt").write_bytes(b"\xff\xfe")
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    assert backend.resolve_caption() == ("やわらかい声。", "config")


class FakeDiT:
    """encode_conditions_full の差し替えを確かめるための DiT の代わり。"""

    def encode_conditions_full(self, **kwargs):
        return ("text", "text_mask", "speaker", "speaker_mask", "caption", "caption_mask")


class MixModel(FakeModel):
    """generate の中で、差し込まれた話者の表現が encode_conditions_full から出てくるかを記録する。"""

    def __init__(self, result):
        super().__init__(result)
        self.model = FakeDiT()
        self.config = type(
            "Config", (), {"dit": type("DiT", (), {"speaker_patch_size": 4, "latent_dim": 32})}
        )()
        self.seen = []
        self.encoded = []

    def _encode_ref_audios(self, audios):
        self.encoded.append(audios)
        return "latent", "mask"

    def _load_ref_waveform(self, audio):
        return audio[None] if audio.ndim == 1 else audio

    def generate(self, text, **kwargs):
        refs = self._encode_ref_audios(["wav"])
        with_ref = self.model.encode_conditions_full(ref_latent="ref")
        without_ref = self.model.encode_conditions_full(ref_latent=None)
        self.seen.append((refs, with_ref, without_ref))
        return super().generate(text, **kwargs)


def test_speak_with_mix_injects_weighted_speaker_state(no_ref_config, tmp_path, monkeypatch):
    import mlx.core as mx

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    states = {a: mx.ones((1, 3, 2)), b: mx.full((1, 5, 2), 3.0)}
    monkeypatch.setattr(backend, "_speaker_state", lambda path: states[path])

    backend.speak("テスト", caption="女性の声。", mix=[(a, 1.0), (b, 3.0)])

    (refs, with_ref, without_ref) = model.seen[0]
    # 差し込む間は参照音声を符号化しない（空の1パッチで経路だけ通す）
    assert model.encoded == []
    assert np.asarray(refs[0]).shape == (1, 4, 32)
    # 長さは短い方にそろえ、重みは足して1に直して混ぜる: 0.25 * 1 + 0.75 * 3
    np.testing.assert_allclose(np.asarray(with_ref[2]), np.full((1, 3, 2), 2.5))
    assert np.asarray(with_ref[3]).shape == (1, 3)
    # 参照なしの呼び出し（継続時間の予測の空の話者など）には差し込まない
    assert without_ref[2] == "speaker"
    call = model.calls[0]
    assert call["ref_audio"] == str(a)
    assert call["caption"] == "女性の声。"
    # 合成が終わったら差し込みを外す。次のふつうの読み上げに混ざらない
    assert backend._injected_speaker is None


def test_speak_injects_the_profile_speaker_state_measured_once(config, tmp_path, monkeypatch):
    import mlx.core as mx

    config = dataclasses.replace(config, split_sentences=True)

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)
    measured = []

    def speaker_state(path):
        measured.append(path)
        return mx.ones((1, 3, 2))

    monkeypatch.setattr(backend, "_reference_state", speaker_state)

    list(backend.speak_sentences("一つ目。二つ目。"))

    # 声は読み上げの最初に1回だけ決め、参照音声を文ごとに符号化し直さない
    profile = ProfileStore(tmp_path / "profiles").active()
    assert profile is not None
    assert measured == [profile.reference]
    assert model.encoded == []
    assert [np.asarray(with_ref[2]).shape for _, with_ref, _ in model.seen] == [(1, 3, 2)] * 2
    assert model.calls[0]["ref_audio"] == str(profile.reference)
    assert backend._injected_speaker is None


def test_resolve_audio_stays_inside_data_root(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)
    take = tmp_path / "generations" / "take.wav"
    take.parent.mkdir(parents=True, exist_ok=True)
    take.write_bytes(b"RIFF")
    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(b"RIFF")

    assert backend.resolve_audio("generations/take.wav") == take.resolve()
    for bad in ["../outside.wav", "generations/missing.wav", str(outside)]:
        with pytest.raises(AudioNotFound):
            backend.resolve_audio(bad)


def test_speak_with_mix_refuses_when_model_cannot_take_injection(
    no_ref_config, tmp_path, monkeypatch
):
    # 差し込む口が無いモデルで黙ってふつうの声を鳴らすと、2択の A と B が同じ声になる
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_mixed_speaker_state", lambda mix, unmuffle=0.0: "state")

    with pytest.raises(RuntimeError):
        backend.speak("テスト", mix=[(tmp_path / "a.wav", 1.0)])
    assert model.calls == []


def test_speak_with_negative_mix_weight_extrapolates(no_ref_config, tmp_path, monkeypatch):
    import mlx.core as mx

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    states = {a: mx.full((1, 2, 2), 1.0), b: mx.full((1, 2, 2), 3.0)}
    monkeypatch.setattr(backend, "_speaker_state", lambda path: states[path])

    backend.speak("テスト", mix=[(a, 1.5), (b, -0.5)])

    # 1.5 * 1 - 0.5 * 3 = 0。a から b と逆の向きへ伸ばした表現
    (_, with_ref, _) = model.seen[0]
    np.testing.assert_allclose(np.asarray(with_ref[2]), np.zeros((1, 2, 2)))


def _write_styles(tmp_path, styles):
    import json

    (tmp_path / "styles.json").write_text(
        json.dumps({"styles": styles}, ensure_ascii=False), encoding="utf-8"
    )


def _profile(tmp_path, name, caption):
    return ProfileStore(tmp_path / "profiles").create(
        name=name,
        caption=caption,
        source="design",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
    )


def test_speak_with_style_uses_its_caption_and_sampler(no_ref_config, tmp_path, monkeypatch):
    _profile(tmp_path, "声", "プロファイルの話し方。")
    _write_styles(
        tmp_path,
        [{"name": "ささやき", "caption": "ささやく。", "sampler": {"duration_scale": 1.2}}],
    )
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト", style="ささやき")

    (call,) = model.calls
    assert call["caption"] == "ささやく。"
    assert call["duration_scale"] == 1.2
    # スタイルが触らない項目は config.toml のまま
    assert call["cfg_scale_speaker"] == 2.5


def test_request_caption_and_sampler_override_style(no_ref_config, tmp_path, monkeypatch):
    _profile(tmp_path, "声", "プロファイルの話し方。")
    _write_styles(
        tmp_path,
        [
            {
                "name": "s",
                "caption": "スタイル。",
                "sampler": {"duration_scale": 1.2, "num_steps": 60},
            }
        ],
    )
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト", caption="引数。", sampler={"duration_scale": 0.9}, style="s")

    (call,) = model.calls
    assert call["caption"] == "引数。"
    assert call["duration_scale"] == 0.9
    assert call["num_steps"] == 60


def test_style_without_caption_keeps_profile_caption(no_ref_config, tmp_path, monkeypatch):
    _profile(tmp_path, "声", "プロファイルの話し方。")
    _write_styles(tmp_path, [{"name": "速く", "caption": None, "sampler": {"duration_scale": 0.8}}])
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト", style="速く")

    (call,) = model.calls
    assert call["caption"] == "プロファイルの話し方。"
    assert call["duration_scale"] == 0.8


def test_unknown_style_raises_before_generating(no_ref_config, tmp_path, monkeypatch):
    from pairvoice.styles import StyleNotFound

    _profile(tmp_path, "声", "話し方。")
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    with pytest.raises(StyleNotFound):
        backend.speak("テスト", style="無い")

    assert model.calls == []


def test_speak_with_profile_name_uses_that_profile(no_ref_config, tmp_path, monkeypatch):
    other = _profile(tmp_path, "試す声", "試すときの声。")
    _profile(tmp_path, "使用中の声", "使用中の声。")
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)

    backend.speak("テスト", profile_id="試す声")

    (call,) = model.calls
    assert call["ref_audio"] == str(other.reference)


def test_speak_sentences_reads_text_without_end_marks_as_one_sentence(
    config, tmp_path, monkeypatch
):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    list(backend.speak_sentences("区切りなし"))

    assert [(call["text"], call.get("duration_scale")) for call in model.calls] == [
        ("区切りなし", None)
    ]


def test_speak_sentences_reads_whole_text_at_once_by_default(config, tmp_path, monkeypatch):
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    parts = list(backend.speak_sentences("一つ目。二つ目。"))

    assert [(call["text"], call.get("duration_scale")) for call in model.calls] == [
        ("一つ目。二つ目。", None)
    ]
    assert len(parts) == 1


def test_speak_sentences_shortens_all_but_the_last_sentence(config, tmp_path, monkeypatch):
    config = dataclasses.replace(config, split_sentences=True)
    model = FakeModel(FakeResult(np.linspace(-0.5, 0.5, 4800, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    parts = list(backend.speak_sentences("一つ目。二つ目。三つ目。"))

    assert [call["text"] for call in model.calls] == ["一つ目。", "二つ目。", "三つ目。"]
    assert [call.get("duration_scale") for call in model.calls] == [
        NON_FINAL_DURATION_SCALE,
        NON_FINAL_DURATION_SCALE,
        None,
    ]
    assert len({part.path for part in parts}) == 3


def test_speak_sentences_scales_the_configured_duration(config, tmp_path, monkeypatch):
    config = dataclasses.replace(
        config,
        split_sentences=True,
        sampler=dataclasses.replace(config.sampler, duration_scale=1.2),
    )
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    list(backend.speak_sentences("一つ目。二つ目。"))

    assert [call["duration_scale"] for call in model.calls] == [
        pytest.approx(1.2 * NON_FINAL_DURATION_SCALE),
        1.2,
    ]


def test_join_concatenates_parts_into_one_wav(config, tmp_path, monkeypatch):
    config = dataclasses.replace(config, split_sentences=True)
    model = FakeModel(FakeResult(np.linspace(-0.5, 0.5, 48000, dtype=np.float32)))
    backend = make_backend(config, model, tmp_path, monkeypatch)
    parts = list(backend.speak_sentences("一つ目。二つ目。"))

    joined = backend.join(parts)

    assert joined.path not in {part.path for part in parts}
    assert joined.relative_path == f"generations/{joined.path.name}"
    assert joined.duration == pytest.approx(sum(part.duration for part in parts))
    with wave.open(str(joined.path)) as wav:
        frames = wav.getnframes()
    assert frames == sum(round(part.duration * 48000) for part in parts)
    assert backend.join(parts[:1]) == parts[0]


def test_speak_reads_a_lead_and_cuts_it_off(config, tmp_path, monkeypatch):
    # 文頭の語を崩さないよう前置きを読ませ、その後の間から本文だけを残す
    body = 0.3 * np.sin(np.linspace(0, 400 * np.pi, 48000, dtype=np.float32))
    model = FakeModel(FakeResult(body))
    backend = make_backend(config, model, tmp_path, monkeypatch)

    result = backend.speak("テスト")

    assert [(call["text"], call["lead"]) for call in model.calls] == [("テスト", True)]
    assert result.duration == pytest.approx(1.05, abs=0.01)


def test_speak_resynthesizes_without_lead_when_no_gap_follows_it(config, tmp_path, monkeypatch):
    # 間が無ければ前置きを残さずには切れないので、前置きなしで合成し直す
    model = FakeModel(FakeResult(np.zeros(480, dtype=np.float32)), speaks_lead=False)
    backend = make_backend(config, model, tmp_path, monkeypatch)

    backend.speak("テスト")

    assert [(call["text"], call["lead"]) for call in model.calls] == [
        ("テスト", True),
        ("テスト", False),
    ]


@pytest.mark.parametrize(("made_with", "uses_tokens"), [("fake/tts", True), ("other/tts", False)])
def test_speak_uses_folded_tokens_only_from_the_same_model(
    no_ref_config, tmp_path, monkeypatch, made_with, uses_tokens
):
    import mlx.core as mx

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    tokens = np.arange(6, dtype=np.float32).reshape(3, 2)
    profile = ProfileStore(tmp_path / "profiles").create(
        name="畳み込み",
        caption="",
        source="curate",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
        speaker=SpeakerTokens(lambda path: np.save(path, tokens), made_with),
    )
    measured = []

    def speaker_state(path):
        measured.append(path.name)
        return mx.zeros((1, 1, 2))

    monkeypatch.setattr(backend, "_reference_state", speaker_state)

    backend.speak("テスト", profile_id=profile.id)

    # 表現は作ったモデルの空間のものなので、別のモデルなら参照音声で読む
    injected = np.asarray(model.seen[0][1][2])
    if uses_tokens:
        assert measured == []
        np.testing.assert_allclose(injected, tokens[None])
    else:
        assert measured == ["reference.wav"]
        assert injected.shape == (1, 1, 2)


def test_speaker_tokens_encodes_samples_as_one_batch(no_ref_config, tmp_path, monkeypatch):
    import mlx.core as mx

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(
        backend,
        "_encode_speaker",
        lambda waveform: seen.append(waveform.shape) or mx.ones((1, 4, 2)),
    )

    tokens = backend.speaker_tokens(np.zeros(4800, np.float32))

    # ファイルから読むときと同じ (1, samples) の形で符号化し、(T, 次元) で返す
    assert seen == [(1, 4800)]
    assert tokens.shape == (4, 2)


def test_reference_state_is_kept_next_to_the_reference_across_reloads(
    no_ref_config, tmp_path, monkeypatch
):
    import mlx.core as mx

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    model._load_ref_waveform = lambda audio: audio
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    profile = _profile(tmp_path, "声", "")
    assert profile.reference is not None
    encoded = []

    def encode(waveform):
        encoded.append(waveform)
        return mx.full((1, 3, 2), 0.5, dtype=mx.float16)

    monkeypatch.setattr(backend, "_encode_speaker", encode)
    first = backend._reference_state(profile.reference)
    # モデルを降ろして読み直しても（メモリの控えが消えても）、参照音声から作り直さない
    backend.unload()
    backend.load()
    second = backend._reference_state(profile.reference)

    assert encoded == [str(profile.reference)]
    assert second.dtype == mx.float16
    np.testing.assert_array_equal(np.asarray(second), np.asarray(first))
    saved = list(profile.reference.parent.glob("*.speaker.npy"))
    assert len(saved) == 1
    assert "fake--tts" in saved[0].name


def _mix_material(tmp_path, name, *, tokens=None, made_with="fake/tts"):
    return ProfileStore(tmp_path / "profiles").create(
        name=name,
        caption="",
        source="curate" if tokens is not None else "upload",
        write_reference=lambda path: path.write_bytes(b"RIFF"),
        speaker=None
        if tokens is None
        else SpeakerTokens(lambda path: np.save(path, tokens), made_with),
    )


def test_speak_mixes_voices_with_the_states_they_are_read_with(
    no_ref_config, tmp_path, monkeypatch
):
    import mlx.core as mx

    model = MixModel(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = make_backend(no_ref_config, model, tmp_path, monkeypatch)
    folded = _mix_material(tmp_path, "畳み込み", tokens=np.full((4, 2), 3.0, np.float32))
    plain = _mix_material(tmp_path, "取り込み")
    monkeypatch.setattr(backend, "_reference_state", lambda path: mx.ones((1, 5, 2)))

    backend.speak("テスト", mix=[(MixVoice("取り込み"), 1.0), (MixVoice(folded.id), 3.0)])

    # 畳み込んだ声は畳み込んだ表現、ほかの声は参照音声の表現で混ぜる（読み上げと同じ）
    # 長さは短い方にそろえる: 0.25 * 1 + 0.75 * 3
    np.testing.assert_allclose(np.asarray(model.seen[0][1][2]), np.full((1, 4, 2), 2.5))
    assert model.calls[0]["ref_audio"] == str(plain.reference)


def test_mix_tokens_returns_the_mixed_state_as_saved_tokens(no_ref_config, tmp_path, monkeypatch):
    import mlx.core as mx

    backend = make_backend(no_ref_config, MixModel(None), tmp_path, monkeypatch)
    _mix_material(tmp_path, "a")
    _mix_material(tmp_path, "b")
    states = iter([mx.zeros((1, 3, 2)), mx.full((1, 3, 2), 4.0)])
    monkeypatch.setattr(backend, "_reference_state", lambda path: next(states))

    tokens = backend.mix_tokens([(MixVoice("a"), 0.5), (MixVoice("b"), 0.5)])

    # 畳み込んだ声の speaker.npy と同じ (T, 768) の形
    np.testing.assert_allclose(tokens, np.full((3, 2), 2.0))


def test_unmuffle_adds_the_muffle_direction_to_every_frame(no_ref_config, tmp_path, monkeypatch):
    import mlx.core as mx

    backend = make_backend(no_ref_config, MixModel(None), tmp_path, monkeypatch)
    _mix_material(tmp_path, "a")
    monkeypatch.setattr(backend, "_reference_state", lambda path: mx.zeros((1, 3, 2)))
    direction = tmp_path / "muffle.npz"
    np.savez(
        direction, high=np.array([1.0, -2.0], np.float32), boom=np.array([0.5, 0.0], np.float32)
    )
    monkeypatch.setattr("pairvoice.tts.muffle_direction_path", lambda model: direction)

    tokens = backend.mix_tokens([(MixVoice("a"), 1.0)], unmuffle=1.5)

    assert backend.can_unmuffle
    # 上の帯域を戻す向きを足し、低音の膨らみの向きを BOOM_RATIO 倍だけ引く
    expected = 1.5 * (np.array([1.0, -2.0]) - BOOM_RATIO * np.array([0.5, 0.0]))
    np.testing.assert_allclose(tokens, np.tile(expected, (3, 1)), rtol=1e-6)


def test_unmuffle_refuses_model_without_direction(no_ref_config, tmp_path, monkeypatch):
    import mlx.core as mx

    backend = make_backend(no_ref_config, MixModel(None), tmp_path, monkeypatch)
    _mix_material(tmp_path, "a")
    monkeypatch.setattr(backend, "_reference_state", lambda path: mx.zeros((1, 3, 2)))
    monkeypatch.setattr("pairvoice.tts.muffle_direction_path", lambda model: tmp_path / "無い.npz")

    assert not backend.can_unmuffle
    with pytest.raises(UnmuffleUnsupported):
        backend.mix_tokens([(MixVoice("a"), 1.0)], unmuffle=1.0)
    # 足さないなら向きは要らない
    np.testing.assert_allclose(backend.mix_tokens([(MixVoice("a"), 1.0)]), np.zeros((3, 2)))


def test_mix_refuses_random_and_missing_voices(no_ref_config, tmp_path, monkeypatch):
    backend = make_backend(no_ref_config, MixModel(None), tmp_path, monkeypatch)
    a, b = _mix_material(tmp_path, "a"), _mix_material(tmp_path, "b")
    ProfileStore(tmp_path / "profiles").create_random(name="ランダム", members=[a.id, b.id])

    # ランダムの声はメンバーを1つ選んでしまい、混ぜた声が試すたびに変わる
    with pytest.raises(ProfileRejected):
        backend.mix_tokens([(MixVoice("ランダム"), 1.0)])
    with pytest.raises(ProfileNotFound):
        backend.mix_tokens([(MixVoice("無い声"), 1.0)])


# --- 合成しながら鳴らす（speak_streaming）---

# 本物のデコードが1チャンクで確定させる長さ（46 フレーム、1.84 秒）
PIECE = 46 * 1920


class StreamingModel(FakeModel):
    """本物のデコードと同じく、返す波形を解けた順に聞き手（speedups.streaming）へ渡す。"""

    def __init__(self, result, speaks_lead=True, raw=None):
        super().__init__(result, speaks_lead)
        self.dacvae = types.SimpleNamespace()
        # 前置きの付け方を真似ず、この波形をそのまま返す
        self.raw = raw
        # 呼び手に渡った波形の列。generate() が返る時点で何個渡っていたかを覚える
        self.emitted = []
        self.emitted_before_return = []

    def generate(self, text, **kwargs):
        (result,) = super().generate(text, **kwargs)
        if self.raw is not None:
            result = FakeResult(self.raw)
        audio = np.asarray(result.audio, dtype=np.float32)
        sink = getattr(self.dacvae, speedups._SINK, None)
        if sink is not None:
            for start in range(0, len(audio), PIECE):
                piece = audio[start : start + PIECE]
                sink(piece, len(audio) - start - len(piece))
        self.emitted_before_return.append(len(self.emitted))
        return iter([result])


def _saved(result):
    with wave.open(str(result.path)) as reader:
        pcm = np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")
    return pcm.astype(np.float32) / 32767


def _streaming_backend(config, tmp_path, monkeypatch, body, **model_kwargs):
    model = StreamingModel(FakeResult(body), **model_kwargs)
    backend = make_backend(config, model, tmp_path, monkeypatch)
    return backend, model


def _speak_streaming(backend, model, text="テスト"):
    model.emitted.clear()
    result = backend.speak_streaming(text, None, None, None, model.emitted.append)
    return result, list(model.emitted)


def test_speak_streaming_waits_for_the_whole_wave_the_first_time_a_voice_speaks(
    config, tmp_path, monkeypatch
):
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0))

    result, emitted = _speak_streaming(backend, model)

    # そろえる音量は全部を解き終わるまで決まらない。初めての声は wav と同じ波形を一度に渡す
    assert model.emitted_before_return == [0]
    assert len(emitted) == 1
    np.testing.assert_allclose(emitted[0], _saved(result), atol=1 / 32767)


def test_speak_streaming_hands_out_waves_while_the_rest_is_still_decoding(
    config, tmp_path, monkeypatch
):
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0))
    _speak_streaming(backend, model)

    result, emitted = _speak_streaming(backend, model)

    # 2回目からは、覚えた音量で、解けた所から渡す。generate() が返る前に全部渡っている
    assert model.emitted_before_return[-1] == len(emitted) > 1
    # 最初に渡るのは、前置きを切った後の本文の頭から最初のチャンクの終わりまで
    assert len(emitted[0]) == PIECE - int(0.55 * 48000)
    np.testing.assert_allclose(np.concatenate(emitted), _saved(result), atol=1 / 32767)


def test_speak_streaming_saves_the_same_wav_as_speak(config, tmp_path, monkeypatch):
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0))
    whole = backend.speak("テスト")

    streamed, emitted = _speak_streaming(backend, model)

    assert len(emitted) > 1
    assert streamed.path.read_bytes() == whole.path.read_bytes()
    assert streamed.duration == whole.duration


def test_speak_learns_the_level_so_that_the_next_reading_can_stream(config, tmp_path, monkeypatch):
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0))
    backend.speak("試聴")

    _, emitted = _speak_streaming(backend, model)

    assert model.emitted_before_return[-1] == len(emitted) > 1


def test_speak_streaming_levels_with_what_the_voice_needed_before(config, tmp_path, monkeypatch):
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0, amplitude=0.6))
    loud, _ = _speak_streaming(backend, model)
    model.result = FakeResult(_tone(6.0, amplitude=0.15))

    quiet, emitted = _speak_streaming(backend, model)

    # wav は発話ごとにそろえるので同じ大きさになる。渡す波形は、この声で前にそろえた倍率を
    # そのまま掛けるので、小さく合成された発話は小さいまま（1/4）になる
    assert np.abs(_saved(quiet)).max() == pytest.approx(np.abs(_saved(loud)).max(), rel=0.01)
    ratio = np.abs(np.concatenate(emitted)).max() / np.abs(_saved(quiet)).max()
    assert ratio == pytest.approx(0.25, rel=0.01)


def test_speak_streaming_keeps_a_level_per_voice_and_caption(config, tmp_path, monkeypatch):
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0))
    _speak_streaming(backend, model)
    model.emitted.clear()

    # 同じ声でも、話し方（caption）が違えば大きさも違いうる。初めての組み合わせとして扱う
    backend.speak_streaming("テスト", "ささやく。", None, None, model.emitted.append)

    assert model.emitted_before_return[-1] == 0
    assert len(model.emitted) == 1


def test_speak_streaming_resynthesizes_without_lead_and_streams_that(config, tmp_path, monkeypatch):
    backend, model = _streaming_backend(
        config, tmp_path, monkeypatch, _tone(4.0), speaks_lead=False
    )
    _speak_streaming(backend, model)
    model.calls.clear()

    result, emitted = _speak_streaming(backend, model)

    # 前置きの後に間が無ければ何も渡さずに作り直し、作り直した方を解けた所から渡す
    assert [call["lead"] for call in model.calls] == [True, False]
    assert model.emitted_before_return[-2:] == [0, len(emitted)]
    assert len(emitted) > 1
    np.testing.assert_allclose(np.concatenate(emitted), _saved(result), atol=1 / 32767)


def test_speak_streaming_passes_the_whole_wave_when_the_decoder_does_not_stream(
    config, tmp_path, monkeypatch
):
    # 差し替え（speedups.install）が入らないモデルでは、解けた順に受け取れない
    model = FakeModel(FakeResult(_tone(6.0)))
    backend = make_backend(config, model, tmp_path, monkeypatch)
    backend.speak("試聴")
    emitted = []

    result = backend.speak_streaming("テスト", None, None, None, emitted.append)

    assert len(emitted) == 1
    np.testing.assert_allclose(emitted[0], _saved(result), atol=1 / 32767)


def test_speak_streaming_does_not_resynthesize_once_it_has_started_playing(
    config, tmp_path, monkeypatch
):
    # 小さな声の前置きと本文の後に、35dB より大きい音が来る。途中までの音では前置きを切れるが、
    # 全体を見ると前置きも本文の頭も「無音」の扱いになり、前置きを切れない
    quiet = 0.005
    raw = np.concatenate(
        [
            _tone(0.3, amplitude=quiet),
            np.zeros(int(0.3 * 48000), np.float32),
            _tone(3.4, amplitude=quiet),
            _tone(2.0, amplitude=0.9),
        ]
    )
    backend, model = _streaming_backend(config, tmp_path, monkeypatch, _tone(6.0))
    _speak_streaming(backend, model)
    model.raw = raw
    model.calls.clear()

    result, emitted = _speak_streaming(backend, model)

    # 鳴らし始めた後では作り直さない（作り直すと、鳴らした音と別の読み上げが残る）
    assert [call["lead"] for call in model.calls] == [True]
    assert model.emitted_before_return[-1] == len(emitted) > 1
    assert result.duration > 0


def test_backend_reports_the_sample_rate_of_the_loaded_model(config, tmp_path, monkeypatch):
    class Model24k(FakeModel):
        sample_rate = 24000

    model = Model24k(FakeResult(np.zeros(480, dtype=np.float32)))
    backend = MlxAudioBackend(config, data_dir=tmp_path)
    assert backend.sample_rate == 48000

    monkeypatch.setattr(backend, "_load_model", lambda: model)
    backend.load()

    assert backend.sample_rate == 24000
