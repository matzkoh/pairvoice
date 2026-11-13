import subprocess

import pytest

from pairvoice import updates
from pairvoice.updates import UpdateChecker, UpgradeError, is_newer, upgrade


def test_is_newer_compares_by_order():
    assert is_newer("0.21.0", "0.20.0")
    assert is_newer("0.10.0", "0.9.0")
    assert not is_newer("0.20.0", "0.20.0")
    # 手元でバージョンを上げて公開する前は PyPI より進む。そのとき知らせない
    assert not is_newer("0.20.0", "0.21.0")
    assert not is_newer("壊れたバージョン", "0.20.0")


async def test_checker_reports_the_latest_version():
    checker = UpdateChecker("0.20.0", fetch=lambda: "0.21.0")
    assert checker.state == {"latest": None, "available": False}
    await checker.check()
    assert checker.state == {"latest": "0.21.0", "available": True}


async def test_checker_keeps_the_last_result_when_offline():
    answers = iter(["0.21.0"])

    def fetch():
        try:
            return next(answers)
        except StopIteration:
            raise OSError("offline") from None

    checker = UpdateChecker("0.20.0", fetch=fetch)
    await checker.check()
    await checker.check()
    assert checker.state == {"latest": "0.21.0", "available": True}


def runner(failing=()):
    ran = []

    def run(command):
        ran.append(command[1:])
        code = 1 if command[1:3] in failing else 0
        return subprocess.CompletedProcess(command, code, stdout="", stderr="だめだった\n")

    return ran, run


def test_upgrade_installs_the_latest_server_then_the_plugin(monkeypatch):
    monkeypatch.setattr(updates, "find_command", lambda name: f"/bin/{name}")
    ran, run = runner()
    assert upgrade(run) == []
    assert ran == [
        ["tool", "install", "pairvoice@latest"],
        ["plugin", "marketplace", "update", "pairvoice"],
        ["plugin", "update", "pairvoice@pairvoice"],
    ]


def test_upgrade_stops_when_the_server_cannot_be_installed(monkeypatch):
    monkeypatch.setattr(updates, "find_command", lambda name: f"/bin/{name}")
    ran, run = runner(failing=[["tool", "install"]])
    with pytest.raises(UpgradeError, match="だめだった"):
        upgrade(run)
    assert len(ran) == 1


def test_upgrade_needs_uv(monkeypatch):
    monkeypatch.setattr(updates, "find_command", lambda name: None)
    with pytest.raises(UpgradeError, match="uv"):
        upgrade(runner()[1])


def test_upgrade_reports_a_plugin_it_could_not_update(monkeypatch):
    monkeypatch.setattr(
        updates, "find_command", lambda name: f"/bin/{name}" if name == "uv" else None
    )
    assert upgrade(runner()[1]) == ["claude が見つからないので、プラグインは手で更新する"]

    monkeypatch.setattr(updates, "find_command", lambda name: f"/bin/{name}")
    (note,) = upgrade(runner(failing=[["plugin", "update"]])[1])
    assert "プラグインを更新できなかった" in note


def test_find_command_asks_the_login_shell_when_not_on_path(monkeypatch):
    monkeypatch.setattr(updates.shutil, "which", lambda name: None)
    # 対話シェルは端末の制御文字などを同じ行に混ぜる
    noise = "\x1b]1337;ShellIntegrationVersion=12\x07"
    answers = {"uv": "/Users/me/.local/bin/uv", "missing": ""}

    def run(command, **kwargs):
        name = command[-1].rsplit(" ", 1)[-1].rstrip(')"')
        stdout = f"{noise}{updates._MARK}{answers[name]}{updates._MARK}"
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr(updates.subprocess, "run", run)
    assert updates.find_command("uv") == "/Users/me/.local/bin/uv"
    assert updates.find_command("missing") is None
