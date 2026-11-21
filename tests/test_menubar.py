import os
import signal
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from pairvoice import menubar

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=JST)


def health(*, llm="loaded", tts="loaded", reason=None, until=None, detail="", playback=None):
    """/health の応答のうち、menubar が読む項目だけを組む。"""
    return {
        "ok": True,
        "llm": {
            "model": "m",
            "state": llm,
            "detail": detail if llm in menubar.BROKEN_STATES else "",
        },
        "tts": {
            "model": "t",
            "state": tts,
            "detail": detail if tts in menubar.BROKEN_STATES else "",
        },
        "mute": {"active": reason is not None, "reason": reason, "until": until},
        "queue": {"running": 0, "waiting": 0},
        "playback": playback or {"playing": False, "waiting": 0},
    }


def test_remaining_minutes_rounds_up():
    until = (NOW + timedelta(seconds=90)).isoformat()
    assert menubar.remaining_minutes(until, NOW) == 2


def test_remaining_minutes_never_goes_negative():
    until = (NOW - timedelta(minutes=5)).isoformat()
    assert menubar.remaining_minutes(until, NOW) == 0


def test_remaining_minutes_without_until():
    assert menubar.remaining_minutes(None, NOW) is None


def test_state_line_for_manual_mute_shows_remaining():
    until = (NOW + timedelta(minutes=28)).isoformat()
    assert (
        menubar.describe_state(health(reason="manual", until=until), NOW) == "ミュート中 残り 28 分"
    )


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("microphone", "マイク使用中のため自動でミュート"),
        ("audio_output", "音声の再生中のため自動でミュート"),
    ],
)
def test_state_line_for_auto_mute(reason, message):
    assert menubar.describe_state(health(reason=reason), NOW) == message


def test_state_line_when_idle():
    assert menubar.describe_state(health(), NOW) == "読み上げ有効"


def test_state_line_when_unreachable():
    assert menubar.describe_state(None, NOW) == "pairvoice が応答しない"


def test_state_line_for_broken_model_includes_detail():
    payload = health(tts="misconfigured", detail="profile_missing")
    assert menubar.describe_state(payload, NOW) == "tts: misconfigured（profile_missing）"


def test_state_line_for_broken_model_without_detail():
    payload = health(llm="failed")
    assert menubar.describe_state(payload, NOW) == "llm: failed"


def test_models_line():
    assert menubar.models_line(health(llm="loading")) == "llm: loading / tts: loaded"


def test_models_line_when_unreachable():
    assert menubar.models_line(None) == ""


def find(items, action):
    return next(item for item in items if item.action == action)


def test_menu_warns_when_plugin_and_server_versions_differ():
    def labels(plugin):
        items = menubar.menu_spec({**health(), "version": "0.8.0", "plugin": plugin}, NOW)
        return [item.label for item in items if item.action is None]

    warning = "プラグイン 0.6.0 以前 と pairvoice 0.8.0 のバージョンが違う"
    assert warning in labels({"version": "0.6.0 以前", "mismatch": True})
    assert warning not in labels({"version": "0.8.0", "mismatch": False})
    # 古い常駐サーバーは plugin を返さない
    assert len(labels(None)) == len(labels({"version": None, "mismatch": False}))


def test_menu_offers_an_update_only_when_pypi_is_ahead():
    def update_items(update, updating=False):
        payload = {**health(), **({"update": update} if update is not None else {})}
        items = menubar.menu_spec(payload, NOW, updating)
        return [
            item
            for item in items
            if item.action in ("update", "release_notes") or item.label == "更新中…"
        ]

    update, notes = update_items({"latest": "0.21.0", "available": True})
    assert (update.label, update.action, update.enabled) == (
        "pairvoice 0.21.0 に更新する",
        "update",
        True,
    )
    assert (notes.label, notes.action, notes.enabled) == ("変更点を見る", "release_notes", True)
    assert update_items({"latest": "0.20.0", "available": False}) == []
    # 古い常駐サーバーは update を返さない
    assert update_items(None) == []
    # 更新している間は押せない
    (item,) = update_items({"latest": "0.21.0", "available": True}, updating=True)
    assert (item.label, item.enabled) == ("更新中…", False)


def test_perform_update_upgrades_then_restarts(monkeypatch):
    calls = []
    monkeypatch.setattr(menubar.updates, "upgrade", lambda: calls.append("upgrade") or [])
    monkeypatch.setattr(menubar, "alert", lambda message, *args, **kwargs: calls.append(message))
    monkeypatch.setattr(menubar.launchd, "restart", lambda: calls.append("restart") or 0)
    menubar.perform("update", "http://127.0.0.1:17495")
    assert calls == ["upgrade", "pairvoice を更新した", "restart"]


def test_perform_update_does_not_restart_when_the_upgrade_fails(monkeypatch):
    def fail():
        raise menubar.updates.UpgradeError("uv が見つからない")

    calls = []
    monkeypatch.setattr(menubar.updates, "upgrade", fail)
    monkeypatch.setattr(
        menubar, "alert", lambda message, detail="", **kwargs: calls.append((message, detail))
    )
    monkeypatch.setattr(menubar.launchd, "restart", lambda: calls.append("restart") or 0)
    menubar.perform("update", "http://127.0.0.1:17495")
    assert calls == [("更新できなかった", "uv が見つからない")]


def test_perform_release_notes_opens_the_releases_of_the_repository(monkeypatch):
    opened = []
    monkeypatch.setattr(
        menubar.subprocess, "Popen", lambda command, **kwargs: opened.append(command)
    )
    menubar.perform("release_notes", "http://127.0.0.1:17495")
    assert opened == [["open", "https://github.com/matzkoh/pairvoice/releases"]]


def test_check_update_tells_the_result(monkeypatch):
    shown = []
    monkeypatch.setattr(
        menubar, "alert", lambda message, *args, **kwargs: shown.append(message) or "OK"
    )
    monkeypatch.setattr(
        menubar.client, "call", lambda *args, **kwargs: {"latest": "0.20.0", "available": False}
    )
    menubar.perform("check_update", "http://127.0.0.1:17495")

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(menubar.client, "call", offline)
    menubar.perform("check_update", "http://127.0.0.1:17495")
    assert shown == ["pairvoice は最新（0.20.0）", "アップデートを確かめられなかった"]


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("更新する", ["update"]), ("変更点を見る", ["notes"]), ("あとで", [])],
)
def test_check_update_acts_on_the_button_pressed(monkeypatch, choice, expected):
    done = []
    monkeypatch.setattr(
        menubar.client, "call", lambda *args, **kwargs: {"latest": "0.21.0", "available": True}
    )
    monkeypatch.setattr(menubar, "alert", lambda *args, **kwargs: choice)
    monkeypatch.setattr(menubar, "update", lambda: done.append("update"))
    monkeypatch.setattr(menubar, "open_release_notes", lambda: done.append("notes"))
    menubar.perform("check_update", "http://127.0.0.1:17495")
    assert done == expected


def test_menu_lists_mute_presets():
    items = menubar.menu_spec(health(), NOW)
    assert [item.label for item in items if item.action and item.action.startswith("mute:")] == [
        "15分だけ黙らせる",
        "30分だけ黙らせる",
        "1時間だけ黙らせる",
    ]


def test_fetch_health_gets_health_with_a_short_timeout(monkeypatch):
    calls = []

    def record(base, path, *, method="POST", body=None, timeout=30):
        calls.append((path, method, timeout))
        return {"ok": True}

    monkeypatch.setattr(menubar.client, "call", record)
    assert menubar.fetch_health("http://127.0.0.1:17495") == {"ok": True}
    assert calls == [("/health", "GET", 2.0)]


def test_fetch_health_returns_none_when_unreachable(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(menubar.client, "call", refuse)
    assert menubar.fetch_health("http://127.0.0.1:17495") is None


@pytest.mark.parametrize(
    ("action", "path", "body"),
    [
        ("mute:15", "/mute", {"minutes": 15}),
        ("mute:30", "/mute", {"minutes": 30}),
        ("mute:60", "/mute", {"minutes": 60}),
        ("unmute", "/unmute", None),
        ("stop", "/stop", None),
        ("warmup", "/warmup", None),
    ],
)
def test_perform_posts_to_the_right_endpoint(monkeypatch, action, path, body):
    calls = []

    def record(base, p, *, method="POST", body=None, timeout=30):
        calls.append((p, method, body))
        return {}

    monkeypatch.setattr(menubar.client, "call", record)
    menubar.perform(action, "http://127.0.0.1:17495")
    assert calls == [(path, "POST", body)]


@pytest.mark.parametrize("mode", ["outside", "orbit", "off"])
def test_perform_spatial_rewrites_only_the_config(monkeypatch, tmp_path, mode):
    path = tmp_path / "config.toml"
    path.write_text("# 自分用\n[playback]\nvolume = 0.5\n", encoding="utf-8")
    monkeypatch.setattr(menubar.launchd, "restart", lambda: pytest.fail("再起動しない"))

    menubar.perform(f"spatial:{mode}", "http://127.0.0.1:17495", path)

    assert path.read_text(encoding="utf-8") == (
        f'# 自分用\n[playback]\nvolume = 0.5\nspatial = "{mode}"\n'
    )


def test_perform_restart_restarts_the_server(monkeypatch):
    called = []
    monkeypatch.setattr(menubar.launchd, "restart", lambda: called.append("server") or 0)
    menubar.perform("restart", "http://127.0.0.1:17495")
    assert called == ["server"]


def test_perform_studio_opens_the_page_the_server_serves(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "pairvoice.studio_web.subprocess.Popen", lambda command, **kwargs: opened.append(command)
    )
    menubar.perform("studio", "http://127.0.0.1:17495")
    assert opened == [["open", "http://127.0.0.1:17495/"]]


def test_perform_swallows_http_failure_but_logs_it(monkeypatch, caplog):
    # 失敗しても常駐は続ける。次の /health が本当の状態を持ってくる。
    # ただし記録は残す（未起動と操作側の不具合を後から見分けるため）。
    def fail(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(menubar.client, "call", fail)
    with caplog.at_level("WARNING", logger="pairvoice.menubar"):
        menubar.perform("mute:30", "http://127.0.0.1:17495")
    assert "mute:30" in caplog.text
    assert "boom" in caplog.text


def test_controller_class_definition_is_accepted_by_pyobjc():
    """pyobjc はメソッド名からセレクタを導き、引数の数と突き合わせる。

    Python 側の補助メソッドに @objc.python_method が無いと、クラス定義の時点で
    BadPrototypeError になる（アンダースコア始まりでも ObjC に公開される）。
    ObjC のクラス名は登録制なので、1プロセスで一度だけ組む。
    """
    import AppKit
    import objc

    controller = menubar._controller_class(AppKit, objc)

    assert controller.__name__ == "MenuBarApp"
    assert controller.instanceMethodForSelector_("redraw:") is not None
    assert controller.instanceMethodForSelector_("menuWillOpen:") is not None
    assert controller.instanceMethodForSelector_("onAction:") is not None
    assert controller.instanceMethodForSelector_("initWithBase:parentPid:configPath:") is not None


def test_run_reports_missing_pyobjc(monkeypatch, capsys):
    def no_appkit(name):
        raise ImportError("No module named 'AppKit'")

    monkeypatch.setattr(menubar.importlib, "import_module", no_appkit)
    assert menubar.run("http://127.0.0.1:17495") == 2
    assert "pyobjc-framework-Cocoa" in capsys.readouterr().err


def test_parent_alive_without_parent():
    # 単体起動（`pairvoice menubar` を直に叩く）には親がいない。道連れの相手もいない。
    assert menubar.parent_alive(None)


def test_parent_alive_for_the_actual_parent():
    assert menubar.parent_alive(os.getppid())


def test_parent_alive_rejects_a_living_process_that_is_not_the_parent():
    # 親が死んで PID が別のプロセスに再利用されても、孤児として残らない
    assert not menubar.parent_alive(os.getpid())


def test_parent_alive_detects_a_dead_process():
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    assert not menubar.parent_alive(dead.pid)


def test_request_shutdown_asks_the_parent_to_stop(monkeypatch):
    signalled = []
    monkeypatch.setattr(menubar.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    menubar.request_shutdown(4321)

    assert signalled == [(4321, signal.SIGTERM)]


def test_request_shutdown_without_parent_does_nothing(monkeypatch):
    monkeypatch.setattr(
        menubar.os, "kill", lambda pid, sig: pytest.fail("頼む先が無いので送らないはず")
    )
    menubar.request_shutdown(None)


def test_request_shutdown_swallows_failure_but_logs_it(monkeypatch, caplog):
    def fail(pid, sig):
        raise ProcessLookupError("gone")

    monkeypatch.setattr(menubar.os, "kill", fail)
    with caplog.at_level("WARNING", logger="pairvoice.menubar"):
        menubar.request_shutdown(4321)
    assert "4321" in caplog.text


def test_quit_label_says_it_stops_pairvoice():
    # 「終了」はアイコンだけでなくサーバーも落とす。ラベルでそう伝える。
    items = menubar.menu_spec(health(), NOW)
    assert find(items, "quit").label == "pairvoice を終了"


def test_badge_draws_a_dot_beside_the_icon():
    """描画の手順（合成の切り替え・塗り）が pyobjc で通り、点の分だけ幅が広がる。"""
    import AppKit

    base = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        menubar.ICON_IDLE, None
    )
    badged = menubar._badged(AppKit, base)

    assert badged.size().width > base.size().width
    # 描画の手順は描くときに初めて走るので、ビットマップに起こして確かめる
    assert AppKit.NSBitmapImageRep.imageRepWithData_(badged.TIFFRepresentation()) is not None
