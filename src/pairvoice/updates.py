"""PyPI に新しい版が出ているかを確かめ、頼まれたら常駐サーバーとプラグインを上げる。

確かめるのは常駐サーバーで、起動時と1日ごとに引いて /health に載せる。上げるのは
`pairvoice update` とメニューバーで、片方だけ上げると版がずれるので、プラグインも一緒に上げる。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib import request

from packaging.version import InvalidVersion, Version

_log = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/pairvoice/json"
CHECK_INTERVAL_SECONDS = 86400.0
# 起動直後はモデルの読み込みと並ぶので、少し待ってから引く
FIRST_CHECK_DELAY_SECONDS = 60.0
FETCH_TIMEOUT_SECONDS = 10.0


def fetch_latest() -> str:
    """PyPI の最新の安定版。利用者や機械を見分けられる情報は送らない。"""
    req = request.Request(PYPI_URL, headers={"User-Agent": "pairvoice"})
    with request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read())["info"]["version"]


def is_newer(latest: str, current: str) -> bool:
    """手元の wheel は公開前に PyPI より進むので、!= でなく大小で比べる。読めない版は知らせない。"""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


class UpdateChecker:
    def __init__(self, current: str, fetch: Callable[[], str] | None = None) -> None:
        self._current = current
        # 既定は作る時点で引く。テストは fetch_latest を差し替えて、PyPI に出ていかない
        self._fetch = fetch or fetch_latest
        self.state: dict = {"latest": None, "available": False}

    async def check(self) -> bool:
        """取れたかを返す。オフラインはふつうに起こるので、取れなければ前の結果を残す。"""
        try:
            latest = await asyncio.to_thread(self._fetch)
        except Exception as error:
            _log.debug("PyPI の最新版を取れなかった: %s", error)
            return False
        self.state = {"latest": latest, "available": is_newer(latest, self._current)}
        return True

    async def run(self) -> None:
        await asyncio.sleep(FIRST_CHECK_DELAY_SECONDS)
        while True:
            await self.check()
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


_MARK = "@@pairvoice@@"


class UpgradeError(RuntimeError):
    """常駐サーバーを上げられなかった。"""


def find_command(name: str) -> str | None:
    """自分の PATH（`pairvoice install` の時点のシェルのもの）で探し、無ければ今のログインシェルに聞く。

    install の後に入れたコマンドは自分の PATH に載っていない。PATH を足す設定は .zshrc などの
    対話用の起動ファイルにあることが多いので、-i も付けて読ませる。
    """
    if found := shutil.which(name):
        return found
    shell = os.environ.get("SHELL") or "/bin/zsh"
    try:
        done = subprocess.run(
            [shell, "-ilc", f'printf "{_MARK}%s{_MARK}" "$(command -v {name})"'],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # 対話シェルは起動ファイルやシェル連携の出力（端末の制御文字など）を同じ行にも混ぜるので、目印で囲む
    found = re.search(f"{_MARK}(.*?){_MARK}", done.stdout)
    path = found.group(1) if found else ""
    return path if Path(path).is_absolute() else None


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def upgrade(run: Callable[[list[str]], subprocess.CompletedProcess[str]] = _run) -> list[str]:
    """常駐サーバーを PyPI の最新版に入れ替え、プラグインも上げる。再起動は呼び出し側が行う。

    戻り値は、プラグインを上げられなかったときの知らせ（常駐サーバーは上がっている）。
    """
    uv = find_command("uv")
    if uv is None:
        raise UpgradeError("uv が見つからない")
    # upgrade でなく @latest で入れ直す。wheel のファイルから入れた版も PyPI の版に戻せる
    done = run([uv, "tool", "install", "pairvoice@latest"])
    if done.returncode != 0:
        raise UpgradeError(_tail(done) or "uv tool install が失敗した")

    claude = find_command("claude")
    if claude is None:
        return ["claude が見つからないので、プラグインは手で更新する"]
    for command in (
        [claude, "plugin", "marketplace", "update", "pairvoice"],
        [claude, "plugin", "update", "pairvoice@pairvoice"],
    ):
        done = run(command)
        if done.returncode != 0:
            _log.warning("%s が失敗した: %s", " ".join(command[1:]), _tail(done))
            return ["プラグインを更新できなかった（`claude plugin update pairvoice@pairvoice`）"]
    return []


def _tail(done: subprocess.CompletedProcess[str]) -> str:
    lines = (done.stderr or done.stdout or "").strip().splitlines()
    return lines[-1] if lines else ""
