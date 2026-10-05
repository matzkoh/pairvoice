import signal
import socket
import subprocess
import sys
import time

import pytest

from pairvoice import studio_process


def test_studio_alive_detects_listening_socket():
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    try:
        assert studio_process.studio_alive(port=port) is True
    finally:
        listener.close()


def test_studio_alive_false_when_nothing_listens():
    # 一度 listen して即座に閉じ、誰も使っていないポート番号を得る。
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    assert studio_process.studio_alive(port=port) is False


@pytest.fixture(autouse=True)
def studio_log(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "pairvoice-studio.log"
    monkeypatch.setattr(studio_process.logs, "STUDIO_LOG_PATH", path)
    return path


def record_spawns(monkeypatch, *, alive, node="/opt/homebrew/bin/node"):
    calls = []
    monkeypatch.setattr(studio_process, "studio_alive", lambda: alive)
    monkeypatch.setattr(studio_process, "find_node", lambda: node)
    monkeypatch.setattr(
        studio_process.subprocess,
        "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    return calls


def test_open_studio_opens_browser_when_alive_even_without_node(monkeypatch):
    calls = record_spawns(monkeypatch, alive=True, node=None)
    studio_process.open_studio()
    assert [command for command, _ in calls] == [
        ["open", f"http://127.0.0.1:{studio_process.STUDIO_PORT}"]
    ]


def test_open_studio_spawns_server_from_bundle_root_and_detaches(monkeypatch, studio_log):
    calls = record_spawns(monkeypatch, alive=False)
    studio_process.open_studio()
    command, kwargs = calls[0]
    assert command == [
        "/opt/homebrew/bin/node",
        "studio/server.ts",
        "--port",
        str(studio_process.STUDIO_PORT),
        "--open",
    ]
    assert kwargs["cwd"] == studio_process.BUNDLE_ROOT
    # 呼んだ側が終了しても studio を生かすため、セッションを切り離す。
    assert kwargs["start_new_session"] is True
    # 入出力も切り離す。引き継ぐと、パイプで読んでいる呼び出し側が EOF を受け取れない
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"].name == str(studio_log)
    assert kwargs["stderr"] is subprocess.STDOUT


def test_spawned_studio_does_not_hold_the_callers_pipe(studio_log):
    # 実際に子を起こす。子は眠り続けるので、呼んだ側の出力を引き継いでいれば communicate() は返らない
    sleeper = "sleep 30.4711"
    script = (
        "import pathlib; from pairvoice import studio_process; "
        f"studio_process.logs.STUDIO_LOG_PATH = pathlib.Path({str(studio_log)!r}); "
        f"studio_process._spawn(['sh', '-c', 'echo started; exec {sleeper}'])"
    )
    caller = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE)
    try:
        out, _ = caller.communicate(timeout=10)
        deadline = time.monotonic() + 5
        while not studio_log.read_bytes() and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        caller.kill()
        subprocess.run(["pkill", "-f", sleeper], check=False)
    assert out == b""
    assert studio_log.read_bytes() == b"started\n"


def test_open_studio_raises_without_node(monkeypatch):
    calls = record_spawns(monkeypatch, alive=False, node=None)
    with pytest.raises(studio_process.NodeNotFound):
        studio_process.open_studio()
    assert calls == []


def test_find_node_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(studio_process.shutil, "which", lambda name: None)
    assert studio_process.find_node() is None


def fake_process_table(monkeypatch, *, listening, commands):
    """lsof（listen している PID）と ps（その PID のコマンド）の応答を差し替える。"""

    def run(command, **kwargs):
        if command[0] == "lsof":
            out = "\n".join(str(pid) for pid in listening)
        else:
            out = commands.get(int(command[-1]), "")
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

    monkeypatch.setattr(studio_process.subprocess, "run", run)


def test_studio_pid_finds_our_studio(monkeypatch):
    fake_process_table(
        monkeypatch, listening=[4321], commands={4321: "/opt/node studio/server.ts --port 17494"}
    )
    assert studio_process.studio_pid() == 4321


def test_studio_pid_ignores_other_processes_on_the_port(monkeypatch):
    # 別のプロセスがポートを掴んでいたら、それは止めない
    fake_process_table(monkeypatch, listening=[4321], commands={4321: "python -m http.server"})
    assert studio_process.studio_pid() is None


def test_studio_pid_none_when_nothing_listens(monkeypatch):
    fake_process_table(monkeypatch, listening=[], commands={})
    assert studio_process.studio_pid() is None


def studio_restart_harness(monkeypatch, *, pid, stays_alive=False, node="/opt/homebrew/bin/node"):
    events = []
    alive = {"value": True}
    monkeypatch.setattr(studio_process, "studio_pid", lambda: pid)
    monkeypatch.setattr(studio_process, "find_node", lambda: node)

    def kill(target, sig):
        events.append(("kill", target, sig))
        alive["value"] = stays_alive

    monkeypatch.setattr(studio_process.os, "kill", kill)
    monkeypatch.setattr(studio_process, "studio_alive", lambda: alive["value"])
    monkeypatch.setattr(
        studio_process.subprocess,
        "Popen",
        lambda command, **kwargs: events.append(("spawn", command, kwargs)),
    )
    return events


def test_restart_studio_stops_and_respawns_detached_without_opening_browser(monkeypatch):
    events = studio_restart_harness(monkeypatch, pid=4321)

    assert studio_process.restart_studio() is True

    assert events[0] == ("kill", 4321, signal.SIGTERM)
    _, command, kwargs = events[1]
    # 開いているタブは再読み込みすれば済む。--open だとタブが増える
    assert command == [
        "/opt/homebrew/bin/node",
        "studio/server.ts",
        "--port",
        str(studio_process.STUDIO_PORT),
    ]
    assert kwargs["cwd"] == studio_process.BUNDLE_ROOT
    assert kwargs["start_new_session"] is True


def test_restart_studio_does_nothing_when_studio_is_not_running(monkeypatch):
    events = studio_restart_harness(monkeypatch, pid=None)
    assert studio_process.restart_studio() is False
    assert events == []


def test_restart_studio_does_not_spawn_while_port_is_still_taken(monkeypatch, caplog):
    events = studio_restart_harness(monkeypatch, pid=4321, stays_alive=True)
    with caplog.at_level("WARNING", logger="pairvoice.studio_process"):
        assert studio_process.restart_studio(wait_seconds=0) is False
    assert [e[0] for e in events] == ["kill"]
    assert "17494" in caplog.text


def test_restart_studio_without_node_raises_before_stopping(monkeypatch):
    events = studio_restart_harness(monkeypatch, pid=4321, node=None)
    with pytest.raises(studio_process.NodeNotFound):
        studio_process.restart_studio()
    assert events == []


def test_restart_studio_treats_a_studio_that_just_exited_as_stopped(monkeypatch):
    # lsof で見つけてから kill するまでの間に studio が終わっていることがある
    events = studio_restart_harness(monkeypatch, pid=4321)

    def gone(target, sig):
        raise ProcessLookupError

    monkeypatch.setattr(studio_process.os, "kill", gone)
    monkeypatch.setattr(studio_process, "studio_alive", lambda: False)

    assert studio_process.restart_studio() is True
    assert [e[0] for e in events] == ["spawn"]
