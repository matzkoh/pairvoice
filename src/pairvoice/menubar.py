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
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from . import client, launchd, updates
from .config import DEFAULT_CONFIG_PATH, set_value
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


def update_available(health: dict | None) -> bool:
    """新しいバージョンが出ているか。メニューを開かないと気づけないので、アイコンにも点を付ける。"""
    # 古い常駐サーバーは update を返さない
    return bool(((health or {}).get("update") or {}).get("available"))


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
    """プラグインと常駐サーバーのバージョンが食い違うと、API の道筋が合わず読み上げが止まる。"""
    plugin = health.get("plugin") or {}
    if not plugin.get("mismatch"):
        return None
    return f"プラグイン {plugin['version']} と pairvoice {health['version']} のバージョンが違う"


@dataclass(frozen=True)
class MenuItem:
    label: str
    action: str | None
    enabled: bool
    separator: bool = False
    checked: bool = False


SEPARATOR = MenuItem(label="", action=None, enabled=False, separator=True)


def update_items(health: dict, updating: bool) -> list[MenuItem]:
    """PyPI に新しいバージョンが出ていれば、更新する項目と変更点を開く項目を出す。

    更新が済むと常駐サーバーごと再起動する。
    """
    if updating:
        return [MenuItem("更新中…", None, False)]
    if not update_available(health):
        return []
    return [
        MenuItem(f"pairvoice {health['update']['latest']} に更新する", "update", True),
        MenuItem("変更点を見る", "release_notes", True),
    ]


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


def _placement(label: str, mode: str, current: str | None) -> MenuItem:
    on = current == mode
    return MenuItem(label, "spatial:off" if on else f"spatial:{mode}", True, checked=on)


def menu_spec(health: dict | None, now: datetime, updating: bool = False) -> list[MenuItem]:
    """メニューの並びを決める。AppKit を知らないので、そのままテストできる。"""
    reachable = health is not None
    rows = [MenuItem(describe_state(health, now), None, False)]
    if reachable:
        rows.append(MenuItem(models_line(health), None, False))
        if (mismatch := plugin_mismatch_line(health)) is not None:
            rows.append(MenuItem(mismatch, None, False))
        rows.extend(update_items(health, updating))
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

    # 鳴らし方（spatial.py）。2つは背反で、入っている方を押すと切る。
    # 耳元で動かすは隠し機能で、有効にした人の /health にだけ orbit_menu が載る
    if reachable and "spatial" in health:
        spatial = health["spatial"]
        rows.append(_placement("頭の外で鳴らす", "outside", spatial.get("mode")))
        if spatial.get("orbit_menu"):
            rows.append(_placement("耳元で動かす", "orbit", spatial.get("mode")))
        rows.append(SEPARATOR)

    # studio の画面はサーバーが配るので、届かないあいだは開けない
    rows.append(MenuItem("studio を開く", "studio", reachable))
    rows.append(SEPARATOR)

    rows.append(warmup_item(health))
    rows.append(MenuItem("アップデートを確認", "check_update", reachable))
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


def perform(action: str, base: str, config_path: str | None = None) -> None:
    """メニューの動作を実行する。失敗しても常駐は続ける。

    config_path は常駐サーバーが読んだ設定ファイル（/health の config_path）。
    """
    try:
        if action.startswith("mute:"):
            client.call(base, "/mute", body={"minutes": int(action.split(":")[1])}, timeout=5)
        elif action.startswith("spatial:"):
            # 正本は設定ファイル。書き換えれば常駐サーバーが読み直す
            path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH.expanduser()
            set_value(path, "playback", "spatial", action.partition(":")[2])
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
        elif action == "update":
            update()
        elif action == "release_notes":
            open_release_notes()
        elif action == "check_update":
            check_update(base)
    except Exception as failed:
        # 続けるが記録は残す。操作は都度1回なのでログは埋まらず、サーバー未起動と
        # 操作側の不具合を後から見分けられる（モーダルは出さない）。
        _log.warning("メニューの操作 %s に失敗した: %s", action, failed)


def _badged(appkit, base):
    """アイコンの右上の外に点を付ける。点の周りを抜いて、絵と点がくっつかないようにする。"""
    width, height = base.size()
    dot, gap = 5.0, 1.0
    canvas = width + 2.5
    x, y = canvas - dot, height - dot

    def draw(_rect):
        base.drawInRect_(((0, 0), (width, height)))
        context = appkit.NSGraphicsContext.currentContext()
        context.setCompositingOperation_(appkit.NSCompositingOperationClear)
        ring = ((x - gap, y - gap), (dot + 2 * gap, dot + 2 * gap))
        appkit.NSBezierPath.bezierPathWithOvalInRect_(ring).fill()
        context.setCompositingOperation_(appkit.NSCompositingOperationSourceOver)
        appkit.NSColor.blackColor().set()
        appkit.NSBezierPath.bezierPathWithOvalInRect_(((x, y), (dot, dot))).fill()
        return True

    return appkit.NSImage.imageWithSize_flipped_drawingHandler_((canvas, height), False, draw)


def alert(message: str, detail: str = "", buttons: tuple[str, ...] = ("OK",)) -> str:
    """ダイアログを出し、押されたボタンの名前を返す。ワーカーのスレッドから呼ぶ。

    通知センターは使わない。クリックした先を決めるにはバンドル ID を持つアプリが要り、
    osascript で出すと、クリックでスクリプトエディタが開く。
    """
    appkit: Any = importlib.import_module("AppKit")
    app_helper: Any = importlib.import_module("PyObjCTools.AppHelper")
    pressed: list[str] = []
    done = threading.Event()

    def show():
        try:
            # Dock に出ないアプリなので、前に出さないとほかのウィンドウの裏に隠れる
            appkit.NSApp().activateIgnoringOtherApps_(True)
            panel = appkit.NSAlert.alloc().init()
            panel.setMessageText_(message)
            panel.setInformativeText_(detail)
            for button in buttons:
                panel.addButtonWithTitle_(button)
            index = panel.runModal() - appkit.NSAlertFirstButtonReturn
            # 閉じられた（中断された）ときは、最後のボタン（取りやめ）を押したことにする
            pressed.append(buttons[index] if 0 <= index < len(buttons) else buttons[-1])
        finally:
            done.set()

    # AppKit の画面はメインスレッドでしか触れない
    app_helper.callAfter(show)
    done.wait()
    return pressed[0]


def open_release_notes() -> None:
    subprocess.Popen(["open", updates.releases_url()], stdin=subprocess.DEVNULL)


def check_update(base: str) -> None:
    """いま確かめて、結果をダイアログで伝える。新しいバージョンがあれば、その場で更新もできる。"""
    try:
        state = client.call(base, "/update/check", timeout=20)
    except Exception as failed:
        _log.warning("アップデートを確かめられなかった: %s", failed)
        alert("アップデートを確かめられなかった", "PyPI に届かない。ネットワークを確かめる")
        return
    if not state["available"]:
        alert(f"pairvoice は最新（{state['latest']}）")
        return
    choice = alert(
        f"pairvoice {state['latest']} が出ている",
        buttons=("更新する", "変更点を見る", "あとで"),
    )
    if choice == "更新する":
        update()
    elif choice == "変更点を見る":
        open_release_notes()


def update() -> None:
    """常駐サーバーとプラグインを上げて、常駐サーバーを再起動する（このメニューバーも起き直す）。"""
    try:
        notes = updates.upgrade()
    except updates.UpgradeError as failed:
        _log.warning("更新に失敗した: %s", failed)
        alert("更新できなかった", str(failed))
        return
    detail = " ".join(notes) or "Claude Code を起動し直すと、プラグインの更新も効く。"
    alert("pairvoice を更新した", f"{detail}\nOK で pairvoice を再起動する。")
    launchd.restart()


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
            self._updating = False
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
                if update_available(self._health):
                    image = _badged(appkit, image)
                image.setTemplate_(True)
                self._item.button().setImage_(image)

            self._menu.removeAllItems()
            for spec in menu_spec(self._health, now, self._updating):
                if spec.separator:
                    self._menu.addItem_(appkit.NSMenuItem.separatorItem())
                    continue
                item = appkit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    spec.label, "onAction:" if spec.enabled else None, ""
                )
                item.setTarget_(self)
                item.setEnabled_(spec.enabled)
                item.setState_(appkit.NSControlStateValueOn if spec.checked else 0)
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
            # 更新は数十秒かかるので、その間は項目を「更新中…」にして二度押しを防ぐ
            self._updating = action == "update"
            if self._updating:
                self.performSelectorOnMainThread_withObject_waitUntilDone_("redraw:", None, False)
            try:
                perform(action, self._base, (self._health or {}).get("config_path"))
            finally:
                self._updating = False
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
