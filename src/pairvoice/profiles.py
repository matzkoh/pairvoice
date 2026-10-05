"""声のプロファイル（参照音声 + caption の組）の読み書き。

データの置き場所の profiles/ に置き、studio と常駐サーバーの両方が触る。常駐サーバーは
合成のたびに active と profile.json を読むので、studio で切り替えた・採用した版が
再起動なしに効く。
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ACTIVE_FILENAME = "active"
META_FILENAME = "profile.json"
REFERENCE_FILENAME = "reference.wav"
# studio（server.ts）の ID と同じ形。active に書かれた値でディレクトリの外へ出ないよう、
# 読むときもこの形だけを受ける
_ID_PATTERN = re.compile(r"p-[0-9A-Za-z-]+")


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    caption: str
    source: str
    reference: Path


class ProfileNotFound(Exception):
    """指定された ID のプロファイルが無い（studio で消された・ID の取り違え）。"""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class ProfileStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def active(self) -> Profile | None:
        """使用中のプロファイル。active が無い・壊れている・参照音声が無いなら None。"""
        try:
            profile_id = (self.root / ACTIVE_FILENAME).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return None
        return self.get(profile_id)

    def get(self, profile_id: str) -> Profile | None:
        """ID のプロファイル。形が違う・壊れている・参照音声が無いなら None。"""
        # match + $ は末尾の改行を通すので fullmatch にする
        if not _ID_PATTERN.fullmatch(profile_id):
            return None
        directory = self.root / profile_id
        try:
            meta = json.loads((directory / META_FILENAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(meta, dict):
            return None
        reference = directory / REFERENCE_FILENAME
        if not reference.is_file():
            return None
        return Profile(
            id=profile_id,
            name=str(meta.get("name", profile_id)),
            caption=str(meta.get("caption", "")),
            source=str(meta.get("source", "")),
            reference=reference,
        )

    def create(
        self,
        *,
        name: str,
        caption: str,
        source: str,
        write_reference: Callable[[Path], object],
    ) -> Profile:
        """プロファイルを作って使用中にする。参照音声は write_reference が書く。"""
        now = datetime.now(UTC)
        profile_id = f"p-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
        directory = self.root / profile_id
        directory.mkdir(parents=True)
        try:
            write_reference(directory / REFERENCE_FILENAME)
        except BaseException:
            # 失敗のたびに空のディレクトリが残り、studio の一覧に壊れたプロファイルが溜まる
            shutil.rmtree(directory, ignore_errors=True)
            raise
        meta = {
            "name": name,
            "caption": caption,
            "source": source,
            "created_at": now.isoformat(),
        }
        _atomic_write(directory / META_FILENAME, json.dumps(meta, ensure_ascii=False, indent=2))
        _atomic_write(self.root / ACTIVE_FILENAME, profile_id + "\n")
        return Profile(
            id=profile_id,
            name=name,
            caption=caption,
            source=source,
            reference=directory / REFERENCE_FILENAME,
        )
