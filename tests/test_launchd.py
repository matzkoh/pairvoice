from pairvoice import launchd


def test_restart_kickstarts_the_launch_agent(monkeypatch):
    calls = []

    class Result:
        returncode = 0

    monkeypatch.setattr(
        launchd.subprocess, "run", lambda command, **kwargs: calls.append(command) or Result()
    )
    monkeypatch.setattr(launchd.os, "getuid", lambda: 501)

    assert launchd.restart() == 0
    assert calls == [["launchctl", "kickstart", "-k", "gui/501/local.pairvoice"]]
