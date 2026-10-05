"""常駐サーバーの LaunchAgent。cli と menubar（再起動）、install（登録）が共有する。"""

from __future__ import annotations

import os
import subprocess

LABEL = "local.pairvoice"


def domain() -> str:
    return f"gui/{os.getuid()}"


def target() -> str:
    return f"{domain()}/{LABEL}"


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def restart() -> int:
    """常駐サーバーを再起動する。出力は取り込まず、失敗の理由を呼んだ端末に見せる。"""
    return subprocess.run(["launchctl", "kickstart", "-k", target()], check=False).returncode
