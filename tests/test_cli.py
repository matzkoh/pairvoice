import contextlib
import io
import json
import signal
import socket
from email.message import Message
from urllib.error import HTTPError

import pytest

from pairvoice import cli, client
from pairvoice.config import load_config


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def config_path(tmp_path):
    """存在しない設定ファイルのパス。開発者の実 ~/.config/pairvoice/config.toml を
    読ませないために、CLI テストは必ずこれを --config で渡す。
    """
    return str(tmp_path / "missing.toml")


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_urlopen(request, timeout=None):
        recorded.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": json.loads(request.data.decode()) if request.data else None,
                "timeout": timeout,
            }
        )
        return FakeResponse({"ok": True})

    monkeypatch.setattr(client.request, "urlopen", fake_urlopen)
    return recorded


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("30m", 30), ("1h", 60), ("45", 45), ("8h", 480), ("1m", 1)],
)
def test_parse_duration(text, minutes):
    assert cli.parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "abc", "0m", "9h", "-5", "1d", "1.5h"])
def test_parse_duration_rejects_invalid(text):
    with pytest.raises(ValueError, match=r"期間|範囲"):
        cli.parse_duration(text)


def test_mute_posts_minutes(calls, config_path):
    assert cli.main(["--config", config_path, "mute", "30m"]) == 0

    assert calls[0]["url"] == "http://127.0.0.1:17495/mute"
    assert calls[0]["method"] == "POST"
    assert calls[0]["body"] == {"minutes": 30}


def test_unmute_posts_without_body(calls, config_path):
    assert cli.main(["--config", config_path, "unmute"]) == 0

    assert calls[0]["url"] == "http://127.0.0.1:17495/unmute"
    assert calls[0]["body"] == {}


def test_stop_posts(calls, config_path):
    assert cli.main(["--config", config_path, "stop"]) == 0

    assert calls[0]["url"] == "http://127.0.0.1:17495/stop"


def test_warmup_posts(calls, config_path):
    assert cli.main(["--config", config_path, "warmup"]) == 0

    assert calls[0]["url"] == "http://127.0.0.1:17495/warmup"
    # 初回はダウンロードを含むので、既定の 30 秒で打ち切らない
    assert calls[0]["timeout"] == cli.WARMUP_TIMEOUT_SECONDS


def test_status_gets_health(calls, config_path):
    assert cli.main(["--config", config_path, "status"]) == 0

    assert calls[0]["url"] == "http://127.0.0.1:17495/health"
    assert calls[0]["method"] == "GET"


def test_say_bypasses_mute(calls, config_path, monkeypatch):
    def fake_urlopen(request, timeout=None):
        calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": json.loads(request.data.decode()) if request.data else None,
                "timeout": timeout,
            }
        )
        return FakeResponse(
            {"path": "/tmp/x.wav", "relative_path": "generations/x.wav", "duration": 1.0}
        )

    monkeypatch.setattr(client.request, "urlopen", fake_urlopen)

    assert cli.main(["--config", config_path, "say", "テスト"]) == 0
    assert calls[0]["url"] == "http://127.0.0.1:17495/speak"
    # 鳴らすのは常駐サーバー
    assert calls[0]["body"] == {"text": "テスト", "bypass_mute": True}

    args = ["--config", config_path, "say", "テスト", "--voice", "声", "--style", "ささやき"]
    assert cli.main(args) == 0
    assert calls[1]["body"] == {
        "text": "テスト",
        "bypass_mute": True,
        "voice": "声",
        "style": "ささやき",
    }


def test_mute_reports_invalid_duration_without_calling_server(calls, config_path):
    assert cli.main(["--config", config_path, "mute", "9h"]) == 2
    assert calls == []


def test_server_down_returns_exit_code_1(monkeypatch, capsys, config_path):
    def refuse(request, timeout=None):
        raise OSError("Connection refused")

    monkeypatch.setattr(client.request, "urlopen", refuse)

    assert cli.main(["--config", config_path, "status"]) == 1
    assert "pairvoice" in capsys.readouterr().err


def test_http_error_from_server_shows_status_and_body(monkeypatch, capsys, config_path):
    """サーバーが返す 4xx/5xx は「接続できません」に潰さず、ステータスと本文をそのまま出す。

    profile_missing はドキュメント上「初回セットアップで普通に起きる」エラーなので、
    ここが「接続できません」に潰れると原因の切り分けができなくなる。
    """

    def fail(request, timeout=None):
        body = json.dumps({"error": "profile_missing", "detail": "ref_audio not found"}).encode()
        raise HTTPError(request.full_url, 503, "Service Unavailable", Message(), io.BytesIO(body))

    monkeypatch.setattr(client.request, "urlopen", fail)

    assert cli.main(["--config", config_path, "say", "テスト"]) == 1
    err = capsys.readouterr().err
    assert "503" in err
    assert "profile_missing" in err


def test_serve_creates_directories_and_binds_uvicorn_to_configured_host_port(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    captured = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.update(kwargs))

    config_file = tmp_path / "config.toml"
    config_file.write_text("port = 18777\n", encoding="utf-8")
    config = load_config(config_file)

    assert not config.tts.output_dir.exists()

    assert cli._serve(config) == 0

    assert config.tts.output_dir.is_dir()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 18777


def test_serve_logs_to_the_terminal_unless_given_a_log_file(tmp_path, monkeypatch):
    config = _serving(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: captured.append(kwargs["log_config"]))
    monkeypatch.setattr(cli, "_menubar_child", lambda *args: contextlib.nullcontext())
    log_file = tmp_path / "logs" / "pairvoice.log"

    assert cli._serve(config) == 0
    assert cli._serve(config, log_file=log_file) == 0

    assert "file" not in captured[0]["handlers"]
    assert captured[1]["handlers"]["file"]["filename"] == str(log_file)
    assert log_file.parent.is_dir()


def test_serve_reports_port_conflict_without_starting_uvicorn(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *args, **kwargs: pytest.fail("port が衝突しているので uvicorn.run は呼ばれないはず"),
    )

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    try:
        port = blocker.getsockname()[1]
        config_file = tmp_path / "config.toml"
        config_file.write_text(f"port = {port}\n", encoding="utf-8")
        config = load_config(config_file)

        assert cli._serve(config) == 1
        assert "already in use" in capsys.readouterr().err
    finally:
        blocker.close()


def test_restart_subcommand_delegates_to_launchd(monkeypatch, config_path):
    monkeypatch.setattr(cli.launchd, "restart", lambda: 7)
    assert cli.main(["--config", str(config_path), "restart"]) == 7


def test_menubar_subcommand_runs_the_menubar(monkeypatch, config_path):
    from pairvoice import menubar

    seen = []
    monkeypatch.setattr(
        menubar, "run", lambda base, parent_pid: seen.append((base, parent_pid)) or 0
    )
    assert cli.main(["--config", str(config_path), "menubar"]) == 0
    assert seen == [("http://127.0.0.1:17495", None)]


def test_menubar_subcommand_takes_the_parent_pid(monkeypatch, config_path):
    from pairvoice import menubar

    seen = []
    monkeypatch.setattr(menubar, "run", lambda base, parent_pid: seen.append(parent_pid) or 0)
    assert cli.main(["--config", str(config_path), "menubar", "--parent-pid", "4321"]) == 0
    assert seen == [4321]


def test_studio_subcommand_opens_the_page_the_server_serves(monkeypatch, config_path):
    monkeypatch.setattr(client, "call", lambda *args, **kwargs: {"ok": True})
    opened = []
    monkeypatch.setattr(
        "pairvoice.studio_web.subprocess.Popen", lambda command, **kwargs: opened.append(command)
    )

    assert cli.main(["--config", str(config_path), "studio"]) == 0
    assert opened == [["open", "http://127.0.0.1:17495/studio/"]]


def test_studio_subcommand_reports_a_stopped_server(monkeypatch, capsys, config_path):
    def unreachable(*args, **kwargs):
        raise OSError("refused")

    monkeypatch.setattr(client, "call", unreachable)
    monkeypatch.setattr(
        "pairvoice.studio_web.subprocess.Popen", lambda *a, **k: pytest.fail("開かないはず")
    )

    assert cli.main(["--config", str(config_path), "studio"]) == 1
    assert "pairvoice restart" in capsys.readouterr().err


class FakeChild:
    """Popen の代わり。terminate / kill / wait の呼ばれ方だけを記録する。"""

    def __init__(self, *, stubborn=False):
        self.stubborn = stubborn
        self.calls = []
        self.running = True

    def poll(self):
        return None if self.running else 0

    def terminate(self):
        self.calls.append("terminate")
        if not self.stubborn:
            self.running = False

    def kill(self):
        self.calls.append("kill")
        self.running = False

    def wait(self, timeout=None):
        self.calls.append("wait")
        if self.stubborn:
            raise cli.subprocess.TimeoutExpired("menubar", timeout or 0)
        return 0


@pytest.fixture(autouse=True)
def no_real_menubar(monkeypatch):
    """テストが本物のメニューバーを起こさないようにする。

    _serve() は uvicorn を止めても menubar を子として起こす。素で走らせると
    テスト中に実物のアイコンが出るので、既定では偽の子に差し替えておく。
    """
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: FakeChild())


def _serving(tmp_path, monkeypatch):
    """_serve() を uvicorn 抜きで走らせる下ごしらえ。config を返す。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: None)
    config_file = tmp_path / "config.toml"
    config_file.write_text("port = 18778\n", encoding="utf-8")
    return load_config(config_file)


def test_menubar_command_uses_the_same_interpreter_and_carries_the_parent_pid(monkeypatch):
    # uv を経由せず、同じ venv の python を直に使う。
    monkeypatch.setattr(cli.os, "getpid", lambda: 4321)
    assert cli._menubar_command(None) == [
        cli.sys.executable,
        "-m",
        "pairvoice",
        "menubar",
        "--parent-pid",
        "4321",
    ]


def test_menubar_command_puts_the_menubar_log_next_to_the_server_log(tmp_path):
    command = cli._menubar_command(None, tmp_path / "pairvoice.log")
    # 別プロセスが同じファイルを回すと世代が壊れるので、serve とは別のファイルにする
    assert command[-2:] == ["--log-file", str(tmp_path / "pairvoice-menubar.log")]


def test_menubar_subcommand_logs_to_the_given_file(monkeypatch, config_path, tmp_path):
    from pairvoice import menubar

    configured = []
    monkeypatch.setattr(cli.logs, "configure_file", configured.append)
    monkeypatch.setattr(menubar, "run", lambda base, parent_pid: 0)
    log_file = tmp_path / "pairvoice-menubar.log"
    assert cli.main(["--config", str(config_path), "menubar", "--log-file", str(log_file)]) == 0
    assert configured == [log_file]


def test_menubar_command_passes_the_config_path_through(monkeypatch):
    monkeypatch.setattr(cli.os, "getpid", lambda: 4321)
    assert cli._menubar_command("/tmp/other.toml")[:5] == [
        cli.sys.executable,
        "-m",
        "pairvoice",
        "--config",
        "/tmp/other.toml",
    ]


def test_serve_starts_the_menubar_and_stops_it_on_exit(tmp_path, monkeypatch):
    config = _serving(tmp_path, monkeypatch)
    child = FakeChild()
    started = []
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: started.append(command) or child)

    assert cli._serve(config) == 0

    assert started
    assert started[0][:3] == [cli.sys.executable, "-m", "pairvoice"]
    assert "menubar" in started[0]
    assert child.calls == ["terminate", "wait"]


def test_serve_kills_a_menubar_that_ignores_terminate(tmp_path, monkeypatch):
    config = _serving(tmp_path, monkeypatch)
    child = FakeChild(stubborn=True)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: child)

    assert cli._serve(config) == 0

    assert child.calls == ["terminate", "wait", "kill"]


def test_serve_stops_the_menubar_even_when_uvicorn_raises(tmp_path, monkeypatch):
    config = _serving(tmp_path, monkeypatch)
    child = FakeChild()
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: child)

    def explode(app, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("uvicorn.run", explode)

    with pytest.raises(RuntimeError):
        cli._serve(config)

    assert child.calls == ["terminate", "wait"]


def test_serve_keeps_going_when_the_menubar_cannot_start(tmp_path, monkeypatch, capsys):
    # アイコンが出ないだけで読み上げは続ける。読み上げが本体である。
    config = _serving(tmp_path, monkeypatch)
    served = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kwargs: served.append(True))

    def fail(command):
        raise OSError("no such file")

    monkeypatch.setattr(cli.subprocess, "Popen", fail)

    assert cli._serve(config) == 0
    assert served == [True]
    assert "メニューバー" in capsys.readouterr().err


def test_menubar_child_installs_and_restores_signal_handlers(monkeypatch):
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: FakeChild())
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    with cli._menubar_child(None):
        assert all(signal.getsignal(sig) is not before[sig] for sig in before)

    assert all(signal.getsignal(sig) is before[sig] for sig in before)


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_stops_the_menubar_and_exits_zero(monkeypatch, sig):
    # uvicorn は graceful shutdown の後に signal を撃ち直す。既定のハンドラに任せると
    # signal 死になり、LaunchAgent の KeepAlive がクラッシュと見て復活させてしまう。
    child = FakeChild()
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: child)
    codes = []
    monkeypatch.setattr(cli.os, "_exit", lambda code: codes.append(code))

    with cli._menubar_child(None):
        handler = signal.getsignal(sig)
        assert callable(handler)
        handler(sig, None)

    assert child.calls == ["terminate", "wait"]
    assert codes == [0]


def test_serve_does_not_wait_on_a_menubar_that_already_exited(tmp_path, monkeypatch):
    # 子が先に終わっていたら、terminate する相手はもういない。
    config = _serving(tmp_path, monkeypatch)
    child = FakeChild()
    child.running = False
    monkeypatch.setattr(cli.subprocess, "Popen", lambda command: child)

    assert cli._serve(config) == 0
    assert child.calls == []


def test_install_subcommand_passes_the_absolute_config_path(monkeypatch, tmp_path):
    from pairvoice import install

    seen = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(install, "install", lambda config, **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["--config", "missing.toml", "install"]) == 0
    assert seen["config_path"] == tmp_path.resolve() / "missing.toml"


def test_install_subcommand_without_config_passes_none(monkeypatch, tmp_path):
    from pairvoice import install

    seen = {}
    monkeypatch.setattr(cli, "load_config", lambda path: load_config(tmp_path / "missing.toml"))
    monkeypatch.setattr(install, "install", lambda config, **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["install"]) == 0
    assert seen["config_path"] is None


@pytest.fixture
def broken_config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("port = \n", encoding="utf-8")
    return str(path)


def test_broken_config_is_reported_without_traceback(calls, capsys, broken_config):
    assert cli.main(["--config", broken_config, "status"]) == 2
    assert broken_config in capsys.readouterr().err
    assert calls == []


def test_commands_that_do_not_read_config_survive_a_broken_one(monkeypatch, broken_config):
    from pairvoice import install

    monkeypatch.setattr(install, "uninstall", lambda: 0)
    monkeypatch.setattr(cli.launchd, "restart", lambda: 0)

    assert cli.main(["--config", broken_config, "uninstall"]) == 0
    assert cli.main(["--config", broken_config, "restart"]) == 0


def test_install_subcommand_keeps_a_symlinked_config_path(monkeypatch, tmp_path):
    # load_config は設定ファイルのディレクトリを基準に相対パスを解く。リンクを辿ると、
    # シェルと launchd で別のディレクトリを基準にしてしまう
    from pairvoice import install

    real = tmp_path / "dotfiles"
    real.mkdir()
    (real / "config.toml").write_text("", encoding="utf-8")
    link = tmp_path / "config.toml"
    link.symlink_to(real / "config.toml")
    seen = {}
    monkeypatch.setattr(install, "install", lambda config, **kwargs: seen.update(kwargs) or 0)

    assert cli.main(["--config", str(link), "install"]) == 0
    assert seen["config_path"] == link
