"""版の履歴（要約のプロンプトと、声の caption）。

<dir>/<kind>-<ISO 時刻の : と . を - にしたもの>.txt に1版ずつ置く。studio（history.ts）は
この名前の時刻から「いま動いている版がいつ動き出したか」を読むので、形を変えない。

不変条件は「かつて動いていた版はすべて履歴にある」。そのため書いた"後"に、書いた内容
そのもので撮る。書く"前"の版を残す作りだと、いま動いている版が常に履歴から漏れる。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from .files import atomic_write

# 版を置くディレクトリの名前（データの置き場所の下と、声ごとの下）
HISTORY_DIRNAME = "history"


class InvalidVersion(ValueError):
    """版の名前の形が違う（別の種類の版や、置き場所の外を指す名前）。"""


class VersionNotFound(LookupError):
    """その名前の版が無い。"""


def snapshot(kind: str, directory: Path, content: str) -> None:
    # JavaScript の toISOString() と同じミリ秒までの UTC
    now = datetime.now(UTC)
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S-") + f"{now.microsecond // 1000:03d}Z"
    atomic_write(directory / f"{kind}-{stamp}.txt", content)


def list_versions(kind: str, directory: Path) -> list[dict[str, str]]:
    """新しい順。ts は名前から取った表示用の文字列で、日時としては読めない。"""
    try:
        names = [p.name for p in directory.iterdir()]
    except OSError:
        return []  # まだ1版も無い
    prefix = f"{kind}-"
    versions = sorted(
        (n for n in names if n.startswith(prefix) and n.endswith(".txt")), reverse=True
    )
    return [{"name": n, "ts": n[len(prefix) : -len(".txt")]} for n in versions]


def read_version(kind: str, directory: Path, name: str) -> str:
    # 名前の形を絞るので / や .. を含められず、置き場所の外は指せない
    if not re.fullmatch(rf"{kind}-[0-9TZ-]+\.txt", name):
        raise InvalidVersion(name)
    try:
        return (directory / name).read_text(encoding="utf-8")
    except OSError as missing:
        raise VersionNotFound(name) from missing
