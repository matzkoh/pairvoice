import wave

import numpy as np
import pytest

from pairvoice.config import ProfileConfig, SamplerConfig, TTSConfig
from pairvoice.profiles import ProfileNotFound, ProfileStore
from pairvoice.tts import ANCHOR_TEXT, MlxAudioBackend


class FakeResult:
    def __init__(self, samples, sample_rate=48000, peak=1234):
        self.audio = samples
        self.sample_rate = sample_rate
        self.peak_memory_usage = peak


class FakeModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def generate(self, text, **kwargs):
        self.calls.append({"text": text, **kwargs})
        return iter([self.result])


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
    assert result.duration == pytest.approx(1.0, abs=0.01)
    assert result.peak_memory_gb == 1234

    with wave.open(str(result.path)) as wav:
        assert wav.getframerate() == 48000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 48000


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
        k: v for k, v in call.items() if k not in ("text", "ref_audio", "caption")
    } == backend.resolve_sampler()
    assert "ref_text" not in call  # Irodori-TTS に参照テキストのパラメータは無い
    assert "stream" not in call


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

    # fixture が指定した2項目だけが残る。残りは渡さないことでモデル既定に委ねる
    assert backend.resolve_sampler() == {
        "cfg_guidance_mode": "alternating",
        "cfg_scale_speaker": 2.5,
    }


def test_resolve_sampler_overlays_override_on_config(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    resolved = backend.resolve_sampler({"num_steps": 60, "cfg_scale_speaker": 4.0})

    assert resolved == {
        "cfg_guidance_mode": "alternating",  # 上書きされていない項目は config のまま
        "cfg_scale_speaker": 4.0,
        "num_steps": 60,
    }


def test_resolve_sampler_disables_speaker_kv_rollback_when_scale_is_set(config, tmp_path):
    backend = MlxAudioBackend(config, data_dir=tmp_path)

    # モデル既定（0.9）のままだと mlx-audio が巻き戻しで落ちる（理由は resolve_sampler
    # のコメント）。None を連れていくのはこのときだけで、他の項目は動かさない
    assert backend.resolve_sampler({"speaker_kv_scale": 0.8}) == {
        "cfg_guidance_mode": "alternating",
        "cfg_scale_speaker": 2.5,
        "speaker_kv_scale": 0.8,
        "speaker_kv_min_t": None,
    }
    # スケール未指定なら巻き戻しの分岐に入らないので連れていかない
    assert "speaker_kv_min_t" not in backend.resolve_sampler()


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
    assert {k: v for k, v in call.items() if k not in ("text", "ref_audio", "caption")} == {
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
