"""声のプロファイル（参照音声 + caption の組）の読み書き。

データの置き場所の profiles/ に置き、常駐サーバーだけが書く（studio の画面は API を呼ぶ）。
合成のたびに active と profile.json を読むので、API で切り替えた・書き換えた版が
再起動なしに効く。
"""

from __future__ import annotations

import dataclasses
import json
import re
import secrets
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import history
from .files import atomic_write

ACTIVE_FILENAME = "active"
META_FILENAME = "profile.json"
REFERENCE_FILENAME = "reference.wav"
PROFILES_DIRNAME = "profiles"
# active や URL の ID でディレクトリの外へ出ないよう、読むときもこの形だけを受ける
_ID_PATTERN = re.compile(r"p-[0-9A-Za-z-]+")
# design = caption や合成した声から作った、upload = 手持ちの wav を取り込んだ、
# auto / import = 初回に常駐サーバーが作った
SOURCES = ("design", "upload", "auto", "import")

# 読んで書き戻す処理を1本の列で走らせる。重なると、後から書いた側が先の変更を読む前の
# 内容で上書きして消す（名前と caption を続けて変えたとき、消した後に書き戻したとき等）
_write_lock = threading.RLock()


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    caption: str
    source: str
    reference: Path
    created_at: str = ""

    def describe(self) -> dict[str, str]:
        """API が返す形。参照音声のパスは見せない。"""
        return {
            "id": self.id,
            "name": self.name,
            "caption": self.caption,
            "source": self.source,
            "created_at": self.created_at,
        }


class ProfileNotFound(Exception):
    """指定された ID のプロファイルが無い（消された・ID の取り違え）。"""


class ProfileInUse(Exception):
    """使用中のプロファイルは消せない。消すと次の読み上げで既定の声が黙って作り直される。"""


class TakeRejected(ValueError):
    """他の声の参照音声はテイクにできない。"""


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
        source = meta.get("source")
        return Profile(
            id=profile_id,
            name=str(meta.get("name", profile_id)),
            caption=str(meta.get("caption", "")),
            source=source if source in SOURCES else "upload",
            reference=reference,
            created_at=str(meta.get("created_at", "")),
        )

    def require(self, profile_id: str) -> Profile:
        profile = self.get(profile_id)
        if profile is None:
            raise ProfileNotFound(profile_id)
        return profile

    def all(self) -> list[Profile]:
        """読めるプロファイルすべて。ID（作った日時）の古い順。"""
        if not self.root.is_dir():
            return []
        found = (self.get(child.name) for child in sorted(self.root.iterdir()) if child.is_dir())
        return [profile for profile in found if profile is not None]

    def find(self, key: str) -> Profile | None:
        """ID か名前でプロファイルを引く。同じ名前が複数あれば、いちばん新しく作ったもの。

        API で声を選ぶときに使う。ID は覚えにくいので、名前でも引けるようにする
        """
        by_id = self.get(key)
        if by_id is not None:
            return by_id
        named = [profile for profile in self.all() if profile.name == key]
        return named[-1] if named else None

    def create(
        self,
        *,
        name: str,
        caption: str,
        source: str,
        write_reference: Callable[[Path], object],
    ) -> Profile:
        """プロファイルを作る。参照音声は write_reference が書く。

        使用中のプロファイルがまだ無ければ、作ったものを使用中にする。放っておくと次の
        読み上げで既定の声が自動で作られ、作った声が使われない
        """
        now = datetime.now(UTC)
        profile_id = f"p-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
        directory = self.root / profile_id
        directory.mkdir(parents=True)
        try:
            write_reference(directory / REFERENCE_FILENAME)
        except BaseException:
            # 失敗のたびに空のディレクトリが残り、一覧に壊れたプロファイルが溜まる
            shutil.rmtree(directory, ignore_errors=True)
            raise
        meta = {
            "name": name,
            "caption": caption,
            "source": source,
            "created_at": now.isoformat(),
        }
        _write_meta(directory, meta)
        with _write_lock:
            if self.active() is None:
                atomic_write(self.root / ACTIVE_FILENAME, profile_id + "\n")
        return Profile(
            id=profile_id,
            name=name,
            caption=caption,
            source=source,
            reference=directory / REFERENCE_FILENAME,
            created_at=meta["created_at"],
        )

    def activate(self, profile_id: str) -> Profile:
        with _write_lock:
            profile = self.require(profile_id)
            atomic_write(self.root / ACTIVE_FILENAME, profile.id + "\n")
        return profile

    def update(
        self, profile_id: str, *, name: str | None = None, caption: str | None = None
    ) -> Profile:
        """名前と caption を書き換える。caption は次の読み上げから効き、版を残す。"""
        with _write_lock:
            profile = self.require(profile_id)
            directory = self.root / profile.id
            meta = json.loads((directory / META_FILENAME).read_text(encoding="utf-8"))
            if name is not None:
                meta["name"] = name
            if caption is not None:
                meta["caption"] = caption
            _write_meta(directory, meta)
            if caption is not None:
                history.snapshot("caption", directory / history.HISTORY_DIRNAME, caption)
        return dataclasses.replace(
            profile,
            name=profile.name if name is None else name,
            caption=profile.caption if caption is None else caption,
        )

    def delete(self, profile_id: str) -> None:
        with _write_lock:
            profile = self.require(profile_id)
            active = self.active()
            if active is not None and active.id == profile.id:
                raise ProfileInUse("使用中のプロファイルは削除できません")
            shutil.rmtree(self.root / profile.id, ignore_errors=True)

    def caption_versions(self, profile_id: str) -> list[dict[str, str]]:
        profile = self.require(profile_id)
        return history.list_versions("caption", self._history_dir(profile))

    def restore_caption(self, profile_id: str, version: str) -> Profile:
        profile = self.require(profile_id)
        content = history.read_version("caption", self._history_dir(profile), version)
        # 復元も履歴に1件増える。「その版が再び動き出した」記録として正しい
        return self.update(profile.id, caption=content)

    def _history_dir(self, profile: Profile) -> Path:
        return self.root / profile.id / history.HISTORY_DIRNAME


def _write_meta(directory: Path, meta: dict) -> None:
    atomic_write(directory / META_FILENAME, json.dumps(meta, ensure_ascii=False, indent=2))
