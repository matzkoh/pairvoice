"""データの置き場所のファイルを書く。フックや studio が読んでいる最中のファイルを壊さない。"""

from __future__ import annotations

import secrets
from pathlib import Path


def atomic_write(path: Path, content: str | bytes) -> None:
    """一時ファイルに書いてから差し替える。読む側が書きかけの中身を見ることはない。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    if isinstance(content, bytes):
        tmp.write_bytes(content)
    else:
        tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
