"""studio（Node の server.ts）を起こす、開く、立て直す。メニューバーと CLI が使う。

studio は切り離して起動するので、呼んだ側（menubar や CLI）が終わっても残る。
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import signal
import socket
import subprocess
import time

from . import logs
from .bundle import BUNDLE_ROOT

_log = logging.getLogger(__name__)

STUDIO_PORT = 17494
SERVER_SCRIPT = "studio/server.ts"


class NodeNotFound(RuntimeError):
    def __init__(self) -> None:
        super().__init__("node が見つかりません（studio は Node で動きます）")


def studio_alive(port: int = STUDIO_PORT, timeout: float = 0.3) -> bool:
    """TCP で繋がるかどうかだけを見る。

    studio の GET /api/health は内部で pairvoice に2秒のタイムアウトで問い合わせるので、
    pairvoice が止まっているあいだ応答が遅れる。短いタイムアウトで打ち切ると
    「studio も死んでいる」と読み違え、listen 済みのポートへ二重に起動しにいく。
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def find_node() -> str | None:
    """node の絶対パス。LaunchAgent の PATH には Homebrew も mise も載っていない。"""
    return shutil.which("node")


def _require_node() -> str:
    node_bin = find_node()
    if node_bin is None:
        raise NodeNotFound()
    return node_bin


def _server_command(node_bin: str, *, open_browser: bool) -> list[str]:
    command = [node_bin, SERVER_SCRIPT, "--port", str(STUDIO_PORT)]
    return [*command, "--open"] if open_browser else command


def _spawn(command: list[str]) -> None:
    # 起動した studio は呼んだ側が終了しても生き残らせる（サーバーの再起動でも消えない）。
    # 入出力も呼んだ側から切り離す。引き継ぐと、`pairvoice studio | tail` のようにパイプで
    # 読んでいる側が、studio が終わるまで EOF を受け取れずに止まる
    logs.STUDIO_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with logs.STUDIO_LOG_PATH.open("ab") as log:
        subprocess.Popen(
            command,
            cwd=BUNDLE_ROOT,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )


def open_studio() -> None:
    """動いていればブラウザで開き、動いていなければ起動して開く。"""
    if studio_alive():
        _spawn(["open", f"http://127.0.0.1:{STUDIO_PORT}"])
    else:
        _spawn(_server_command(_require_node(), open_browser=True))


def studio_pid(port: int = STUDIO_PORT) -> int | None:
    """port で listen している studio の PID。

    コマンドに SERVER_SCRIPT を含むものだけを返す。別のプロセスがポートを
    掴んでいたら None にして、それは止めない。
    """
    listening = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    for raw in listening:
        command = subprocess.run(
            ["ps", "-o", "command=", "-p", raw], capture_output=True, text=True, check=False
        ).stdout
        if SERVER_SCRIPT in command:
            return int(raw)
    return None


def restart_studio(wait_seconds: float = 5.0) -> bool:
    """動いている studio を止めて、同じポートで立て直す。立て直したら True。

    動いていなければ何もせず False を返す（起動は open_studio の役目）。
    node が無ければ、止める前に NodeNotFound を投げる。
    """
    pid = studio_pid()
    if pid is None:
        return False
    node_bin = _require_node()
    # lsof で見つけてから止めるまでの間に終わっていれば、止まったものとして立て直す
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_seconds
    while studio_alive():
        if time.monotonic() >= deadline:
            _log.warning("studio（%s 番）が止まらないので立て直さない", STUDIO_PORT)
            return False
        time.sleep(0.1)
    # 開いているタブは再読み込みすれば済むので --open は付けない（付けるとタブが増える）
    _spawn(_server_command(node_bin, open_browser=False))
    return True
