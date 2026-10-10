"""メニューバー常駐。見た目を決める純関数と、AppKit を触る層を分けて持つ。

AppKit の import は run() の中だけで行う。pyobjc が読めない環境でも、
このモジュールと純関数は読める。
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import client, launchd
from .lifecycle import WARMABLE_STATES, ModelState
from .studio_web import open_studio

_log = logging.getLogger(__name__)


MUTE_PRESETS = (15, 30, 60)

ICON_UNREACHABLE = "exclamationmark.triangle"
ICON_MODEL_BROKEN = "exclamationmark.triangle.fill"
ICON_MANUAL = "speaker.slash"
ICON_MIC = "mic.slash"
ICON_OUTPUT = "play.slash"
ICON_IDLE = "speaker.wave.2"

# 状態名の正本は lifecycle.ModelState。/health からは文字列で来るので値で持つ。
BROKEN_STATES = frozenset({ModelState.FAILED.value, ModelState.MISCONFIGURED.value})

_MUTE_ICONS = {
    "manual": ICON_MANUAL,
    "microphone": ICON_MIC,
    "audio_output": ICON_OUTPUT,
}
_AUTO_REASONS = {
    "microphone": "マイク使用中のため自動でミュート",
    "audio_output": "音声の再生中のため自動でミュート",
}


def _broken(health: dict) -> str | None:
    """壊れているモデルの名前を llm から順に探す。見つからなければ None。"""
    for name in ("llm", "tts"):
        if health.get(name, {}).get("state") in BROKEN_STATES:
            return name
    return None


def decide_icon(health: dict | None) -> str:
    if health is None:
        return ICON_UNREACHABLE
    if _broken(health) is not None:
        return ICON_MODEL_BROKEN
    reason = health.get("mute", {}).get("reason")
    return _MUTE_ICONS.get(reason, ICON_IDLE)


def remaining_minutes(until: str | None, now: datetime) -> int | None:
    """期限までの残り分数。過ぎていれば 0 を返す（負の分数を見せない）。"""
    if until is None:
        return None
    seconds = (datetime.fromisoformat(until) - now).total_seconds()
    return max(0, math.ceil(seconds / 60))


def describe_state(health: dict | None, now: datetime) -> str:
    if health is None:
        return "pairvoice が応答しない"

    name = _broken(health)
    if name is not None:
        model = health[name]
        detail = model.get("detail") or ""
        state = model["state"]
        return f"{name}: {state}（{detail}）" if detail else f"{name}: {state}"

    mute = health.get("mute", {})
    reason = mute.get("reason")
    if reason == "manual":
        minutes = remaining_minutes(mute.get("until"), now)
        return "ミュート中" if minutes is None else f"ミュート中 残り {minutes} 分"
    if reason in _AUTO_REASONS:
        return _AUTO_REASONS[reason]
    return "読み上げ有効"


def models_line(health: dict | None) -> str:
    if health is None:
        return ""
    return f"llm: {health['llm']['state']} / tts: {health['tts']['state']}"


def plugin_mismatch_line(health: dict) -> str | None:
    """プラグインと常駐サーバーの版が食い違うと、API の道筋が合わず読み上げが止まる。"""
    plugin = health.get("plugin") or {}
    if not plugin.get("mismatch"):
        return None
    return f"プラグイン {plugin['version']} と pairvoice {health['version']} の版が違う"


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: str | None
    enabled: bool
    separator: bool = False


SEPARATOR = MenuItem(label="", action=None, enabled=False, separator=True)


def _preset_label(minutes: int) -> str:
    return "1時間だけ黙らせる" if minutes == 60 else f"{minutes}分だけ黙らせる"


def warmup_item(health: dict | None) -> MenuItem:
    """事前ロードの項目。押して変わることがあるときだけ押せるようにし、文言で今の状態を伝える。"""
    states = set() if health is None else {health["llm"]["state"], health["tts"]["state"]}
    # warmup は llm → tts の順に読むので、片方が読み込み中なら残りも続けて読まれる
    if ModelState.DOWNLOADING in states:
        label = "モデルをダウンロード中…"
    elif ModelState.LOADING in states:
        label = "モデルをロード中…"
    elif states == {ModelState.LOADED}:
        label = "モデルはロード済み"
    elif ModelState.FAILED in states:
        label = "モデルをロードし直す"
    else:
        label = "モデルを事前ロード"
    busy = ModelState.DOWNLOADING in states or ModelState.LOADING in states
    # misconfigured は設定を直すまで読み込めないので、押せる理由にならない
    return MenuItem(label, "warmup", not busy and bool(states & WARMABLE_STATES))


def menu_spec(health: dict | None, now: datetime) -> list[MenuItem]:
    """メニューの並びを決める。AppKit を知らないので、そのままテストできる。"""
    reachable = health is not None
    rows = [MenuItem(describe_state(health, now), None, False)]
    if reachable:
        rows.append(MenuItem(models_line(health), None, False))
        if (mismatch := plugin_mismatch_line(health)) is not None:
            rows.append(MenuItem(mismatch, None, False))
    rows.append(SEPARATOR)

    playback = health.get("playback", {}) if reachable else {}
    speaking = bool(playback.get("playing") or playback.get("waiting"))
    rows.append(MenuItem("読み上げを止める", "stop", speaking))
    rows.append(SEPARATOR)

    rows.extend(
        MenuItem(_preset_label(minutes), f"mute:{minutes}", reachable) for minutes in MUTE_PRESETS
    )

    manual = reachable and health.get("mute", {}).get("reason") == "manual"
    rows.append(MenuItem("ミュートを解除", "unmute", manual))
    rows.append(SEPARATOR)

    # studio の画面はサーバーが配るので、届かないあいだは開けない
    rows.append(MenuItem("studio を開く", "studio", reachable))
    rows.append(SEPARATOR)

    rows.append(warmup_item(health))
    # 再起動と終了は、サーバーに届かないときこそ押したい項目なので常に有効にする。
    rows.append(MenuItem("再起動", "restart", True))
    rows.append(MenuItem("pairvoice を終了", "quit", True))
    return rows


def fetch_health(base: str, timeout: float = 2.0) -> dict | None:
    """繋がらなければ None。例外を上に出さないので、呼び出し側は状態の判断だけを書ける。

    サーバー未起動は5秒ごとに起こる想定内の状態なので、ここではログを残さない
    （残すと未起動のあいだログが埋まる）。アイコンと状態行がそれを伝える。
    """
    try:
        return client.call(base, "/health", method="GET", timeout=timeout)
    except Exception:
        return None


def parent_alive(parent_pid: int | None) -> bool:
    """親（serve）が生きているか。

    単体起動（`pairvoice menubar` を直に叩いた場合）は親がいないので常に True。
    """
    if parent_pid is None:
        return True
    # serve が直に起こす子なので、親が死ねば launchd に付け替えられて getppid() が変わる。
    # os.kill(pid, 0) だと、親の PID が別のプロセスに再利用されたとき孤児のまま残る
    return os.getppid() == parent_pid


def request_shutdown(parent_pid: int | None) -> None:
    """親（serve）に終了を頼む。「終了」はサーバーごと落とす操作である。

    SIGTERM を受けた親は uvicorn を graceful に降ろし、終了コード 0 で終わる
    （`cli._menubar_child` の signal ハンドラ）。正常終了なので LaunchAgent の
    KeepAlive も復活させない。単体起動には頼む先が無いので、アイコンだけが消える。
    """
    if parent_pid is None:
        return
    try:
        os.kill(parent_pid, signal.SIGTERM)
    except OSError as failed:
        _log.warning("親プロセス %s に終了を伝えられなかった: %s", parent_pid, failed)


def perform(action: str, base: str) -> None:
    """メニューの動作を実行する。失敗しても常駐は続ける。"""
    try:
        if action.startswith("mute:"):
            client.call(base, "/mute", body={"minutes": int(action.split(":")[1])}, timeout=5)
        elif action == "unmute":
            client.call(base, "/unmute", timeout=5)
        elif action == "stop":
            client.call(base, "/stop", timeout=5)
        elif action == "warmup":
            client.call(base, "/warmup", timeout=5)
        elif action == "restart":
            launchd.restart()
        elif action == "studio":
            open_studio(base)
    except Exception as failed:
        # 続けるが記録は残す。操作は都度1回なのでログは埋まらず、サーバー未起動と
        # 操作側の不具合を後から見分けられる（モーダルは出さない）。
        _log.warning("メニューの操作 %s に失敗した: %s", action, failed)


def _controller_class(appkit, objc):
    """NSStatusItem を操る ObjC クラスを組んで返す。

    pyobjc はクラス定義の時点でメソッド名からセレクタを導き、引数の数と突き合わせる。
    Python 側の補助メソッドに @objc.python_method を付け忘れると、ここで
    BadPrototypeError になる。イベントループに入らずに定義だけを確かめられるよう、
    run() から切り出してある（ObjC のクラス名は登録制なので、1プロセスで一度だけ呼ぶ）。
    """

    class MenuBarApp(objc.lookUpClass("NSObject")):
        """NSStatusItem を持ち、menu_spec() の結果を NSMenuItem に変換する。"""

        def initWithBase_parentPid_(self, base, parent_pid):
            self = objc.super(MenuBarApp, self).init()
            self._base = base
            self._parent_pid = parent_pid
            self._health = None
            self._item = appkit.NSStatusBar.systemStatusBar().statusItemWithLength_(
                appkit.NSVariableStatusItemLength
            )
            self._menu = appkit.NSMenu.alloc().init()
            # 既定の自動有効化は、target が action に応答するかどうかで勝手に判断し、
            # setEnabled_(False) を上書きしてしまう。有効無効は menu_spec() が決める。
            self._menu.setAutoenablesItems_(False)
            self._menu.setDelegate_(self)
            self._item.setMenu_(self._menu)
            return self

        # ---- 描画 ----

        def redraw_(self, _ignored):
            now = datetime.now().astimezone()
            image = appkit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                decide_icon(self._health), describe_state(self._health, now)
            )
            if image is not None:
                image.setTemplate_(True)
                self._item.button().setImage_(image)

            self._menu.removeAllItems()
            for spec in menu_spec(self._health, now):
                if spec.separator:
                    self._menu.addItem_(appkit.NSMenuItem.separatorItem())
                    continue
                item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    spec.label, "onAction:" if spec.enabled else None, ""
                )
                item.setTarget_(self)
                item.setEnabled_(spec.enabled)
                item.setRepresentedObject_(spec.action)
                self._menu.addItem_(item)

        # ---- 状態の取得 ----

        @objc.python_method
        def refresh(self):
            # HTTP はワーカーで叩く。サーバー停止中に接続の失敗を待つあいだ
            # メニューが固まるのを避けるため。
            threading.Thread(target=self._fetch, daemon=True).start()

        @objc.python_method
        def _fetch(self):
            self._health = fetch_health(self._base)
            # 描画はメインスレッドでなければならない。
            self.performSelectorOnMainThread_withObject_waitUntilDone_("redraw:", None, False)

        def tick_(self, _timer):
            # 親が SIGKILL されて terminate が届かなかった場合の後追い。
            if not parent_alive(self._parent_pid):
                appkit.NSApp().terminate_(None)
                return
            self.refresh()

        def menuWillOpen_(self, _menu):
            self.refresh()

        # ---- 操作 ----

        def onAction_(self, sender):
            action = sender.representedObject()
            if action == "quit":
                request_shutdown(self._parent_pid)
                appkit.NSApp().terminate_(None)
                return
            threading.Thread(target=self._perform, args=(action,), daemon=True).start()

        @objc.python_method
        def _perform(self, action):
            perform(action, self._base)
            self._fetch()

    return MenuBarApp


def run(base: str, parent_pid: int | None = None) -> int:
    """メニューバーに常駐する。AppKit の import はここだけで行う。

    parent_pid を渡されたら、その親（serve）が消えたときに自分も終了する。
    """
    try:
        # pyobjc は名前を実行時に生やすので、型検査には中身が見えない
        appkit: Any = importlib.import_module("AppKit")
        objc: Any = importlib.import_module("objc")
        app_helper: Any = importlib.import_module("PyObjCTools.AppHelper")
    except ImportError:
        print(
            "メニューバーには pyobjc（pyobjc-framework-Cocoa）が要ります。pairvoice を入れ直してください。",
            file=sys.stderr,
        )
        return 2

    app = appkit.NSApplication.sharedApplication()
    # Dock にアイコンを出さない。アプリバンドルを作らないので LSUIElement は使えない。
    app.setActivationPolicy_(appkit.NSApplicationActivationPolicyAccessory)

    controller = _controller_class(appkit, objc).alloc().initWithBase_parentPid_(base, parent_pid)
    controller.redraw_(None)
    controller.refresh()
    appkit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        5.0, controller, "tick:", None, True
    )
    app_helper.runEventLoop()
    return 0
