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


def test_unreachable_wins_over_everything():
    assert menubar.decide_icon(None) == menubar.ICON_UNREACHABLE


@pytest.mark.parametrize("state", ["failed", "misconfigured"])
def test_broken_model_wins_over_mute(state):
    # 手動ミュート中でも、モデルが壊れていることを先に見せる。
    payload = health(tts=state, reason="manual", until=NOW.isoformat())
    assert menubar.decide_icon(payload) == menubar.ICON_MODEL_BROKEN


@pytest.mark.parametrize("state", ["unloaded", "downloading", "loading", "loaded"])
def test_normal_model_states_are_not_broken(state):
    assert menubar.decide_icon(health(llm=state)) == menubar.ICON_IDLE


@pytest.mark.parametrize(
    ("reason", "icon"),
    [
        ("manual", "ICON_MANUAL"),
        ("microphone", "ICON_MIC"),
        ("audio_output", "ICON_OUTPUT"),
        (None, "ICON_IDLE"),
    ],
)
def test_icon_follows_mute_reason(reason, icon):
    assert menubar.decide_icon(health(reason=reason)) == getattr(menubar, icon)


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


def actions(items):
    """有効な項目の動作だけを並べる。"""
    return [item.action for item in items if item.enabled]


def find(items, action):
    return next(item for item in items if item.action == action)


def test_menu_warns_when_plugin_and_server_versions_differ():
    def labels(plugin):
        items = menubar.menu_spec({**health(), "version": "0.8.0", "plugin": plugin}, NOW)
        return [item.label for item in items if item.action is None]

    warning = "プラグイン 0.6.0 以前 と pairvoice 0.8.0 の版が違う"
    assert warning in labels({"version": "0.6.0 以前", "mismatch": True})
    assert warning not in labels({"version": "0.8.0", "mismatch": False})
    # 古い常駐サーバーは plugin を返さない
    assert len(labels(None)) == len(labels({"version": None, "mismatch": False}))


def test_menu_lists_mute_presets():
    items = menubar.menu_spec(health(), NOW)
    assert [item.label for item in items if item.action and item.action.startswith("mute:")] == [
        "15分だけ黙らせる",
        "30分だけ黙らせる",
        "1時間だけ黙らせる",
    ]


def test_first_two_rows_are_display_only():
    items = menubar.menu_spec(health(), NOW)
    assert items[0].label == "読み上げ有効"
    assert items[1].label == "llm: loaded / tts: loaded"
    assert [items[0].action, items[1].action] == [None, None]
    assert not items[0].enabled
    assert not items[1].enabled


def test_unmute_enabled_only_for_manual_mute():
    manual = menubar.menu_spec(health(reason="manual", until=NOW.isoformat()), NOW)
    assert find(manual, "unmute").enabled


@pytest.mark.parametrize("reason", [None, "microphone", "audio_output"])
def test_unmute_disabled_without_manual_mute(reason):
    # 自動ミュート中に unmute しても MuteController は何も消さない。押せないようにする。
    items = menubar.menu_spec(health(reason=reason), NOW)
    assert not find(items, "unmute").enabled


@pytest.mark.parametrize(
    "playback", [{"playing": True, "waiting": 0}, {"playing": False, "waiting": 2}]
)
def test_stop_enabled_while_speaking(playback):
    items = menubar.menu_spec(health(playback=playback), NOW)
    assert find(items, "stop").enabled


def test_stop_disabled_when_silent():
    items = menubar.menu_spec(health(), NOW)
    assert not find(items, "stop").enabled


def test_unreachable_leaves_only_restart_and_quit():
    items = menubar.menu_spec(None, NOW)
    assert actions(items) == ["restart", "quit"]


def test_studio_opens_only_while_the_server_answers():
    # studio の画面はサーバーが配るので、届かないあいだは開けない
    assert find(menubar.menu_spec(health(), NOW), "studio").enabled
    assert "studio" not in actions(menubar.menu_spec(None, NOW))


def test_menu_has_all_operations_when_healthy():
    # 事前ロードはまだ読み込んでいないときだけ押せる
    items = menubar.menu_spec(health(llm="unloaded", tts="unloaded"), NOW)
    assert actions(items) == [
        "mute:15",
        "mute:30",
        "mute:60",
        "studio",
        "warmup",
        "restart",
        "quit",
    ]


@pytest.mark.parametrize(
    ("llm", "tts", "label", "enabled"),
    [
        ("unloaded", "unloaded", "モデルを事前ロード", True),
        ("loaded", "unloaded", "モデルを事前ロード", True),
        ("downloading", "unloaded", "モデルをダウンロード中…", False),
        ("loaded", "loading", "モデルをロード中…", False),
        ("loaded", "loaded", "モデルはロード済み", False),
        ("failed", "loaded", "モデルをロードし直す", True),
        ("misconfigured", "loaded", "モデルを事前ロード", False),
        ("misconfigured", "unloaded", "モデルを事前ロード", True),
    ],
)
def test_warmup_label_follows_model_states(llm, tts, label, enabled):
    item = find(menubar.menu_spec(health(llm=llm, tts=tts), NOW), "warmup")
    assert (item.label, item.enabled) == (label, enabled)


def test_separators_are_marked():
    items = menubar.menu_spec(health(), NOW)
    assert menubar.SEPARATOR in items
    # 区切り線は separator で表す。action は「押せる動作」だけを持つ。
    assert all(item.action is None and not item.enabled for item in items if item.separator)


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
    assert controller.instanceMethodForSelector_("initWithBase:parentPid:") is not None


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
