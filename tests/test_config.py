import dataclasses
import os
import time
from pathlib import Path

import pytest

from pairvoice.config import (
    Config,
    ConfigError,
    SamplerConfig,
    config_is_stale,
    default_data_root,
    load_config,
)


def test_defaults_when_file_missing(tmp_path):
    config = load_config(tmp_path / "missing.toml")

    assert config.port == 17495
    assert config.llm.model == "mlx-community/gemma-4-e4b-it-8bit"
    assert config.llm.max_tokens == 128
    assert config.tts.model == "mlx-community/Irodori-TTS-v4.1-Small-8bit"
    # サンプラーは全項目 None（モデル既定に任せる）。値の一覧は
    # test_sampler_defaults_to_none_so_model_defaults_apply で見る
    assert config.tts.sampler.cfg_guidance_mode is None
    assert config.tts.sampler.cfg_scale_speaker is None
    assert config.mute.auto_microphone is True
    assert config.mute.auto_audio_output is True
    assert config.mute.audio_output_wait_seconds == 5
    assert config.mute.audio_output_ignore_processes == ()
    assert config.playback.volume == 1.0
    assert config.tts.output_max_age_days == 7
    assert config.eval.style == "any"
    assert config.tts.output_dir.is_absolute()
    assert "~" not in config.tts.output_dir.parts


def test_default_paths_are_absolute_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))

    config = load_config(tmp_path / "missing.toml")

    assert config.tts.output_dir.is_absolute()
    assert config.tts.output_dir.is_relative_to(tmp_path)


def test_overrides_and_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "port = 18000",
                "[llm]",
                'model = "test/llm"',
                "[tts]",
                'output_dir = "~/sounds"',
                "[tts.sampler]",
                "cfg_scale_speaker = 2.5",
                "[tts.profile]",
                'ref_audio = "~/voice/ref.wav"',
                "[mute]",
                "auto_audio_output = true",
                'audio_output_ignore_processes = ["afplay", "Music"]',
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.port == 18000
    assert config.llm.model == "test/llm"
    assert config.llm.max_tokens == 128  # 未指定は既定値のまま
    assert config.tts.output_dir == tmp_path / "sounds"
    assert config.tts.sampler.cfg_scale_speaker == 2.5
    assert config.tts.sampler.cfg_guidance_mode is None  # 未指定はモデル既定に任せる
    assert config.tts.profile.ref_audio == tmp_path / "voice" / "ref.wav"
    assert config.mute.auto_audio_output is True
    assert config.mute.audio_output_ignore_processes == ("afplay", "Music")


def test_playback_reads_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[playback]\nvolume = 0.6\nduck_volume = 0.1\n", encoding="utf-8")

    playback = load_config(path).playback

    assert (playback.volume, playback.duck_volume, playback.max_wait_seconds) == (0.6, 0.1, 60.0)


def test_eval_style_reads_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[eval]\nstyle = "casual"\n', encoding="utf-8")

    assert load_config(path).eval.style == "casual"


def test_empty_ref_audio_means_no_reference(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[tts.profile]\nref_audio = ""\n', encoding="utf-8")

    config = load_config(path)

    assert config.tts.profile.ref_audio is None


def test_config_is_stale_when_file_changed_after_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("port = 17495\n", encoding="utf-8")
    config = load_config(path)

    assert config_is_stale(config) is False

    future = time.time() + 10
    os.utime(path, (future, future))

    assert config_is_stale(config) is True


def test_config_is_not_stale_when_file_absent(tmp_path):
    config = load_config(tmp_path / "missing.toml")

    assert config_is_stale(config) is False


def test_default_profile_does_not_name_a_person():
    config = Config()

    assert config.tts.profile.name == "reference"
    # 参照音声は既定で使わない（caption だけで声を作る VoiceDesign）
    assert config.tts.profile.ref_audio is None
    # 既定の caption は声の描写だけを持つ（特定の人物やキャラクターを指さない）
    assert config.tts.profile.caption.startswith("落ち着いた")


def test_sampler_defaults_to_none_so_model_defaults_apply():
    config = load_config(Path("/nonexistent/config.toml"))

    # None は「モデル既定に任せる」。モデル既定の値を pairvoice 側に写すと二重管理になる
    assert dataclasses.asdict(config.tts.sampler) == {
        "num_steps": None,
        "cfg_guidance_mode": None,
        "cfg_scale_text": None,
        "cfg_scale_caption": None,
        "cfg_scale_speaker": None,
        "t_schedule_mode": None,
        "sway_coeff": None,
        "duration_scale": None,
        "seconds": None,
        "rng_seed": None,
        "cfg_min_t": None,
        "cfg_max_t": None,
        "context_kv_cache": None,
        "speaker_kv_scale": None,
        "truncation_factor": None,
        "rescale_k": None,
        "rescale_sigma": None,
    }


def test_sampler_reads_every_knob_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[tts.sampler]",
                "num_steps = 60",
                'cfg_guidance_mode = "joint"',
                "cfg_scale_text = 2.5",
                "cfg_scale_caption = 4.5",
                "cfg_scale_speaker = 1.5",
                't_schedule_mode = "sway"',
                "sway_coeff = 0.3",
                "duration_scale = 1.1",
                "seconds = 4.0",
                "rng_seed = 7",
                "cfg_min_t = 0.4",
                "cfg_max_t = 0.9",
                "context_kv_cache = false",
                "speaker_kv_scale = 0.8",
                "truncation_factor = 1.2",
                "rescale_k = 0.6",
                "rescale_sigma = 0.7",
            ]
        ),
        encoding="utf-8",
    )

    sampler = load_config(path).tts.sampler

    # 1項目でも dataclass から漏れていれば _build() が黙って捨てるので、全項目を見る
    assert dataclasses.asdict(sampler) == {
        "num_steps": 60,
        "cfg_guidance_mode": "joint",
        "cfg_scale_text": 2.5,
        "cfg_scale_caption": 4.5,
        "cfg_scale_speaker": 1.5,
        "t_schedule_mode": "sway",
        "sway_coeff": 0.3,
        "duration_scale": 1.1,
        "seconds": 4.0,
        "rng_seed": 7,
        "cfg_min_t": 0.4,
        "cfg_max_t": 0.9,
        "context_kv_cache": False,
        "speaker_kv_scale": 0.8,
        "truncation_factor": 1.2,
        "rescale_k": 0.6,
        "rescale_sigma": 0.7,
    }


def test_sampler_config_and_overrides_share_the_same_fields():
    """SamplerConfig（config.toml 側）と SamplerOverrides（/speak 側）は項目を
    増やすときに両方へ1行足す約束になっている。片方だけ足すと、config.toml に
    書いた同じキーが _build() に黙って捨てられる一方 /speak 経由では効いてしまう、
    という非対称な事故が起きる。ここで両者の項目集合と並び（仕様が順序も固定して
    いるため）を強制する。
    """
    from pairvoice.server import SamplerOverrides

    config_fields = [f.name for f in dataclasses.fields(SamplerConfig)]
    override_fields = list(SamplerOverrides.model_fields)

    only_in_config = [name for name in config_fields if name not in override_fields]
    only_in_overrides = [name for name in override_fields if name not in config_fields]
    assert not only_in_config, f"SamplerOverrides に足し忘れている項目: {only_in_config}"
    assert not only_in_overrides, f"SamplerConfig に足し忘れている項目: {only_in_overrides}"

    assert config_fields == override_fields, (
        "SamplerConfig と SamplerOverrides で項目の並びが違う: "
        f"{config_fields} != {override_fields}"
    )


def test_output_dir_follows_data_root_env(tmp_path, monkeypatch):
    # フック・studio と同じくデータの置き場所を PAIRVOICE_DATA_ROOT から決める。
    # サーバーだけ既定の置き場を見ると、生成音声のパスがずれて再生が黙って失敗する
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path / "data"))

    assert default_data_root() == tmp_path / "data"
    assert (
        load_config(tmp_path / "missing.toml").tts.output_dir == tmp_path / "data" / "generations"
    )


def test_default_data_root_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("PAIRVOICE_DATA_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_data_root() == tmp_path / "Library" / "Application Support" / "pairvoice"


def write_config(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_single_ignored_process_is_not_split_into_characters(tmp_path):
    path = write_config(tmp_path, '[mute]\naudio_output_ignore_processes = "zoom.us"\n')

    assert load_config(path).mute.audio_output_ignore_processes == ("zoom.us",)


@pytest.mark.parametrize(
    "text",
    [
        'port = "17495"',
        '[tts]\noutput_max_age_days = "7"',
        '[mute]\naudio_output_wait_seconds = "5"',
        "[llm]\nmax_tokens = true",
        "[llm]\nmax_tokens = 1.5",
        "[tts.sampler]\ncontext_kv_cache = 1",
        "[tts.sampler]\nnum_steps = 1.5",
        "[mute]\naudio_output_ignore_processes = [1]",
        "[tts]\noutput_dir = 1",
        "llm = 3",
    ],
)
def test_wrong_types_are_reported_at_load(tmp_path, text):
    # 読み込みで通すと、使う場面（合成・掃除）で初めて落ちる
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, text))


def test_integers_are_accepted_for_float_fields(tmp_path):
    path = write_config(tmp_path, "[mute]\naudio_output_wait_seconds = 3\n")

    assert load_config(path).mute.audio_output_wait_seconds == 3.0


def test_unknown_keys_are_warned_but_not_fatal(tmp_path, caplog):
    path = write_config(tmp_path, "[llm]\nmax_token = 64\n[unknown]\nx = 1\n")

    with caplog.at_level("WARNING", logger="pairvoice.config"):
        config = load_config(path)

    assert config.llm.max_tokens == 128
    assert "max_token" in caplog.text
    assert "unknown" in caplog.text


def test_relative_paths_resolve_against_the_config_file(tmp_path, monkeypatch):
    # launchd は / で起こすので、作業ディレクトリ基準だと / の下を指す
    home = tmp_path / "conf"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    path = write_config(
        home, '[tts]\noutput_dir = "sounds"\n[tts.profile]\nref_audio = "voice/ref.wav"\n'
    )

    config = load_config(path)

    assert config.tts.output_dir == home / "sounds"
    assert config.tts.profile.ref_audio == home / "voice" / "ref.wav"


def test_config_is_stale_when_replaced_by_an_older_file(tmp_path):
    path = write_config(tmp_path, "port = 17495\n")
    config = load_config(path)

    past = time.time() - 3600
    os.utime(path, (past, past))

    assert config_is_stale(config) is True


def test_relative_data_root_env_is_made_absolute(tmp_path, monkeypatch):
    # フック・studio・launchd（/ で起こす）が同じ場所を指すよう、読んだ時点で絶対パスにする
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", "data")

    assert default_data_root() == Path.cwd() / "data"
