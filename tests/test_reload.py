"""設定ファイルを書き換えると、常駐サーバーが再起動せずに読み直す（Engine.reload_config）。"""

import os

from pairvoice.config import load_config
from pairvoice.lifecycle import Engine
from pairvoice.mute import MuteController
from tests.fakes import FakeCurate, FakeProbe
from tests.test_engine import FakeLLM, FakeSTT, FakeTTS


def rig(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(f'[tts]\noutput_dir = "{tmp_path}/generations"\n' + text, encoding="utf-8")
    config = load_config(path)
    restarts = []
    probe = FakeProbe()
    llm = FakeLLM()
    engine = Engine(
        config=config,
        llm_backend=llm,
        tts_backend=FakeTTS(),
        stt_backend=FakeSTT(),
        curate_backend=FakeCurate(),
        mute=MuteController(config.mute, probe),
        restart=lambda: restarts.append(True),
    )
    return engine, path, restarts, probe, llm


def rewrite(path, text, tmp_path):
    path.write_text(f'[tts]\noutput_dir = "{tmp_path}/generations"\n' + text, encoding="utf-8")
    # 同じ秒のうちに書き換えても mtime が変わるようにする
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_applies_changes_without_restarting(tmp_path):
    engine, path, restarts, probe, llm = rig(tmp_path, "")
    rewrite(
        path,
        '[playback]\nvolume = 0.4\nspatial = "off"\n'
        '[mute]\nmicrophone_ignore_processes = ["Slack"]\n'
        "[llm]\nmax_tokens = 64\n",
        tmp_path,
    )

    engine.reload_config()

    assert engine.config.playback.spatial == "off"
    assert engine.player._config.volume == 0.4
    assert engine.mute._config.microphone_ignore_processes == ("Slack",)
    assert probe.ignored == ((), ("Slack",))
    assert llm.config.max_tokens == 64
    assert engine.health()["config_stale"] is False
    assert engine.health()["spatial"] == {"mode": "off"}
    assert restarts == []


def test_keeps_running_on_a_broken_file_and_waits_for_a_fix(tmp_path, caplog):
    engine, path, restarts, _, _ = rig(tmp_path, '[playback]\nspatial = "orbit"\n')
    rewrite(path, '[playback]\nspatial = "inside"\n', tmp_path)

    engine.reload_config()
    engine.reload_config()

    assert engine.config.playback.spatial == "orbit"
    assert engine.health()["config_stale"] is True
    # 同じ版は1回だけ試す（毎秒ログを埋めない）
    assert caplog.text.count("設定ファイルを読めない") == 1

    rewrite(path, '[playback]\nspatial = "off"\n', tmp_path)
    engine.reload_config()

    assert engine.config.playback.spatial == "off"
    assert restarts == []


def test_restarts_once_when_a_model_changes(tmp_path):
    engine, path, restarts, _, _ = rig(tmp_path, "")
    rewrite(path, '[llm]\nmodel = "another/model"\n', tmp_path)

    engine.reload_config()
    engine.reload_config()

    assert restarts == [True]
    assert engine.health()["config_stale"] is True
    assert engine.config.llm.model != "another/model"


def test_does_nothing_while_the_file_is_unchanged(tmp_path):
    engine, _, restarts, probe, _ = rig(tmp_path, "")
    before = engine.config

    engine.reload_config()

    assert engine.config is before
    assert engine.health()["config_stale"] is False
    assert not hasattr(probe, "ignored")
    assert restarts == []
