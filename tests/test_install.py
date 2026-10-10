import plistlib
import subprocess
import sys
from pathlib import Path

from pairvoice import install, launchd
from pairvoice.config import Config, TTSConfig


def make_examples(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "prompt.example.txt").write_text("雛形のプロンプト", encoding="utf-8")
    (examples / "tone.example.txt").write_text("雛形の口調", encoding="utf-8")
    (examples / "dict.example.tsv").write_text("A\tえー\n", encoding="utf-8")
    return examples


def test_seed_data_root_copies_templates(tmp_path):
    data = tmp_path / "data"

    created = install.seed_data_root(data, make_examples(tmp_path))

    assert (data / "prompt.txt").read_text(encoding="utf-8") == "雛形のプロンプト"
    assert (data / "dict.tsv").read_text(encoding="utf-8") == "A\tえー\n"
    assert (data / "tone.txt").read_text(encoding="utf-8") == "雛形の口調"
    assert created == [data / "prompt.txt", data / "tone.txt", data / "dict.tsv"]


def test_seed_data_root_never_overwrites(tmp_path):
    # 育てたプロンプトと辞書を雛形で潰さない
    data = tmp_path / "data"
    data.mkdir()
    (data / "prompt.txt").write_text("育てたプロンプト", encoding="utf-8")

    created = install.seed_data_root(data, make_examples(tmp_path))

    assert (data / "prompt.txt").read_text(encoding="utf-8") == "育てたプロンプト"
    # 口調を分ける前の prompt.txt は口調を中に持つので、雛形の口調を足さない
    assert created == [data / "dict.tsv"]


def test_launch_agent_runs_this_python_and_keeps_alive_only_on_crash(tmp_path):
    agent = install.launch_agent(
        python="/opt/pairvoice/bin/python",
        path_env="/opt/homebrew/bin:/usr/bin",
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
    )

    assert agent["Label"] == "local.pairvoice"
    # wheel から入れても clone から入れても、いま動いている Python で serve する
    assert agent["ProgramArguments"] == [
        "/opt/pairvoice/bin/python",
        "-m",
        "pairvoice",
        "serve",
        "--log-file",
        str(tmp_path / "pairvoice.log"),
    ]
    # 利用者のシェルの PATH を焼き込む。launchd の既定の PATH には Homebrew も mise も載っていない
    assert agent["EnvironmentVariables"] == {"PATH": "/opt/homebrew/bin:/usr/bin"}
    assert agent["RunAtLoad"] is True
    # 「pairvoice を終了」（終了コード 0）で落としたら戻さない。クラッシュしたときだけ戻す
    assert agent["KeepAlive"] == {"SuccessfulExit": False}
    assert (
        agent["StandardOutPath"]
        == agent["StandardErrorPath"]
        == str(tmp_path / "pairvoice.stderr.log")
    )


def test_launch_agent_bakes_data_root_when_set(tmp_path):
    # launchd から起きるサーバーには利用者のシェルの環境変数が届かない
    agent = install.launch_agent(
        python="/opt/pairvoice/bin/python",
        path_env="/usr/bin",
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
        data_root_env=str(tmp_path / "data"),
    )

    assert agent["EnvironmentVariables"] == {
        "PATH": "/usr/bin",
        "PAIRVOICE_DATA_ROOT": str(tmp_path / "data"),
    }


def test_data_root_mismatch_is_reported(tmp_path):
    config = Config(tts=TTSConfig(output_dir=tmp_path / "elsewhere" / "generations"))

    warning = install.data_root_mismatch(config, tmp_path / "data")

    assert warning is not None
    assert "tts.output_dir" in warning


def test_data_root_match_is_quiet(tmp_path):
    config = Config(tts=TTSConfig(output_dir=tmp_path / "data" / "generations"))

    assert install.data_root_mismatch(config, tmp_path / "data") is None


def recording_launchctl(monkeypatch, *, still_loaded=0):
    """launchctl の呼び出しを記録する。print は still_loaded 回だけ「まだ居る」と答える。"""
    calls = []
    remaining = {"loaded": still_loaded}

    def run(command, **kwargs):
        calls.append(command)
        code = 0
        if command[1] == "print":
            code = 0 if remaining["loaded"] > 0 else 113
            remaining["loaded"] -= 1
        return subprocess.CompletedProcess(command, code, stdout="", stderr="")

    monkeypatch.setattr(launchd.subprocess, "run", run)
    monkeypatch.setattr(install.time, "sleep", lambda seconds: None)
    return calls


def test_install_writes_agent_and_reloads_it(tmp_path, monkeypatch):
    calls = recording_launchctl(monkeypatch)
    agents = tmp_path / "LaunchAgents"
    data = tmp_path / "data"
    config = Config(tts=TTSConfig(output_dir=data / "generations"))

    code = install.install(
        config,
        data_root=data,
        examples_dir=make_examples(tmp_path),
        agents_dir=agents,
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
    )

    assert code == 0
    plist = agents / "local.pairvoice.plist"
    agent = plistlib.loads(plist.read_bytes())
    assert agent["ProgramArguments"][0] == sys.executable
    assert (data / "prompt.txt").exists()
    # 入れ直しに備えて、先に外してから登録する（外す側の失敗は無視する）
    domain = launchd.domain()
    assert calls == [
        ["launchctl", "bootout", f"{domain}/local.pairvoice"],
        ["launchctl", "print", f"{domain}/local.pairvoice"],
        ["launchctl", "bootstrap", domain, str(plist)],
    ]


def test_install_waits_until_the_old_agent_is_gone(tmp_path, monkeypatch):
    # bootout はジョブが消える前に返る。すぐ bootstrap すると「Bootstrap failed: 5」になる
    calls = recording_launchctl(monkeypatch, still_loaded=2)
    data = tmp_path / "data"

    code = install.install(
        Config(tts=TTSConfig(output_dir=data / "generations")),
        data_root=data,
        examples_dir=make_examples(tmp_path),
        agents_dir=tmp_path / "LaunchAgents",
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
    )

    assert code == 0
    assert [c[1] for c in calls] == ["bootout", "print", "print", "print", "bootstrap"]


def test_install_fails_when_bootstrap_fails(tmp_path, monkeypatch):
    def run(command, **kwargs):
        code = {"bootstrap": 5, "print": 113}.get(command[1], 0)
        return subprocess.CompletedProcess(command, code, stdout="", stderr="Bootstrap failed")

    monkeypatch.setattr(launchd.subprocess, "run", run)
    data = tmp_path / "data"

    code = install.install(
        Config(tts=TTSConfig(output_dir=data / "generations")),
        data_root=data,
        examples_dir=make_examples(tmp_path),
        agents_dir=tmp_path / "LaunchAgents",
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
    )

    assert code == 1


def test_uninstall_unloads_and_removes_agent_but_keeps_data(tmp_path, monkeypatch):
    calls = recording_launchctl(monkeypatch)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist = agents / "local.pairvoice.plist"
    plist.write_bytes(b"")

    assert install.uninstall(agents_dir=agents) == 0

    assert not plist.exists()
    assert calls[0] == ["launchctl", "bootout", f"{launchd.domain()}/local.pairvoice"]


def test_default_examples_dir_is_bundled():
    assert (install.EXAMPLES_DIR / "prompt.example.txt").is_file()
    assert isinstance(install.EXAMPLES_DIR, Path)


def test_launch_agent_passes_config_before_the_subcommand(tmp_path):
    # --config はトップレベルの引数なので、serve より前に置く
    agent = install.launch_agent(
        python="/opt/pairvoice/bin/python",
        path_env="/usr/bin",
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
        config_path=tmp_path / "config.toml",
    )

    assert agent["ProgramArguments"][:6] == [
        "/opt/pairvoice/bin/python",
        "-m",
        "pairvoice",
        "--config",
        str(tmp_path / "config.toml"),
        "serve",
    ]


def test_install_bakes_config_path_and_absolute_data_root(tmp_path, monkeypatch):
    recording_launchctl(monkeypatch)
    # launchd は / で起こすので、相対パスのまま焼き込むと別の場所を指す
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", "data")
    agents = tmp_path / "LaunchAgents"
    data = tmp_path / "data"

    install.install(
        Config(tts=TTSConfig(output_dir=data / "generations")),
        data_root=data,
        examples_dir=make_examples(tmp_path),
        agents_dir=agents,
        log_path=tmp_path / "pairvoice.log",
        stderr_path=tmp_path / "pairvoice.stderr.log",
        config_path=tmp_path / "config.toml",
    )

    agent = plistlib.loads((agents / "local.pairvoice.plist").read_bytes())
    assert agent["ProgramArguments"][3:5] == ["--config", str(tmp_path / "config.toml")]
    assert agent["EnvironmentVariables"]["PAIRVOICE_DATA_ROOT"] == str(data)
