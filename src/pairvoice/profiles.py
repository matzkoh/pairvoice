"""声のプロファイル（参照音声 + caption の組）の読み書き。

データの置き場所の profiles/ に置き、常駐サーバーだけが書く（studio の画面は API を呼ぶ）。
合成のたびに active と profile.json を読むので、API で切り替えた・書き換えた版が
再起動なしに効く。

ランダムの声（source が random）は参照音声を持たず、ほかの声（メンバー）を束ねる。
使用中にも `voice` にもでき、要約・合成のたびにメンバーから1つを選んで読む（pick）。
選び方（order）がシャッフルなら、混ぜた順に全員を一巡するまで同じ声を選ばない。
"""

from __future__ import annotations

import dataclasses
import json
import random
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
# 要約の口調（声ごとのものは声のディレクトリに、既定のものはデータの置き場所に置く）
TONE_FILENAME = "tone.txt"
# active や URL の ID でディレクトリの外へ出ないよう、読むときもこの形だけを受ける
_ID_PATTERN = re.compile(r"p-[0-9A-Za-z-]+")
# design = caption や合成した声から作った、upload = 手持ちの wav を取り込んだ、
# auto / import = 初回に常駐サーバーが作った、random = ほかの声から毎回1つを選ぶ
RANDOM_SOURCE = "random"
SOURCES = ("design", "upload", "auto", "import", RANDOM_SOURCE)
# 1つだけでは選ぶ意味が無い
MIN_MEMBERS = 2
# ランダムの声の選び方。random = 毎回ばらばらに選ぶ、shuffle = 混ぜた順に一巡ずつ選ぶ
ORDERS = ("random", "shuffle")

# 読んで書き戻す処理を1本の列で走らせる。重なると、後から書いた側が先の変更を読む前の
# 内容で上書きして消す（名前と caption を続けて変えたとき、消した後に書き戻したとき等）
_write_lock = threading.RLock()

# シャッフルの声ごとの、この巡で残っている声と最後に選んだ声。巡の途中で常駐サーバーが
# 再起動しても混ぜ直すだけなので、ファイルには書かない。ProfileStore はいくつも作られる
# （合成と要約で別）ので、モジュールで持つ
_shuffle_rounds: dict[Path, tuple[list[str], str | None]] = {}
_shuffle_lock = threading.Lock()


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    caption: str
    source: str
    # ランダムの声は持たない
    reference: Path | None
    created_at: str = ""
    # 要約の口調。空なら既定の口調で読む
    tone: str = ""
    # ランダムの声が選ぶ声の ID と、選び方（ORDERS）
    members: tuple[str, ...] = ()
    order: str = "random"

    @property
    def is_random(self) -> bool:
        return self.source == RANDOM_SOURCE

    def describe(self) -> dict[str, object]:
        """API が返す形。参照音声のパスは見せない。"""
        return {
            "id": self.id,
            "name": self.name,
            "caption": self.caption,
            "source": self.source,
            "created_at": self.created_at,
            "tone": self.tone,
            "members": list(self.members),
            "order": self.order,
        }


class ProfileNotFound(Exception):
    """指定された ID のプロファイルが無い（消された・ID の取り違え）。"""


class ProfileInUse(Exception):
    """使用中のプロファイルは消せない。消すと次の読み上げで既定の声が黙って作り直される。"""


class TakeRejected(ValueError):
    """他の声の参照音声はテイクにできない。"""


class ProfileRejected(ValueError):
    """その声には書けない（ランダムの声の caption・口調、メンバーにできない声）。"""


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
        """ID のプロファイル。形が違う・壊れている・参照音声が無いなら None。

        ランダムの声はそのまま返す。読む声が要るときは pick を通す
        """
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
        source = meta.get("source")
        is_random = source == RANDOM_SOURCE
        reference = None if is_random else directory / REFERENCE_FILENAME
        if reference is not None and not reference.is_file():
            return None
        members = meta.get("members") if is_random else None
        order = meta.get("order")
        return Profile(
            id=profile_id,
            name=str(meta.get("name", profile_id)),
            caption="" if is_random else str(meta.get("caption", "")),
            source=source if source in SOURCES else "upload",
            reference=reference,
            created_at=str(meta.get("created_at", "")),
            tone="" if is_random else self.tone(profile_id).read(),
            members=tuple(str(m) for m in members) if isinstance(members, list) else (),
            order=order if order in ORDERS else "random",
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

    def resolve(self, key: str | None) -> Profile | None:
        """API で選ばれた声で読む声。省けば使用中の声（まだ無ければ None）、名前か ID が無ければ ProfileNotFound。

        ランダムの声なら、メンバーから1つを選んで返す
        """
        profile = self.active() if key is None else self.find(key)
        if profile is None:
            if key is None:
                return None
            raise ProfileNotFound(key)
        return self.pick(profile)

    def pick(self, profile: Profile) -> Profile:
        """読む声。ランダムの声ならメンバーから1つを選び、読めるメンバーが無ければ ProfileNotFound。"""
        if not profile.is_random:
            return profile
        deck = random.sample(profile.members, len(profile.members))
        if profile.order != "shuffle":
            return self._first_readable(profile, deck)[1]
        key = self.root / profile.id
        with _shuffle_lock:
            rest, last = _shuffle_rounds.get(key, ([], None))
            # 外したメンバーは残りから除く。足したメンバーは次の巡から入る
            rest = [m for m in rest if m in profile.members]
            # 巡の変わり目で同じ声が続かないよう、前の巡の最後を後ろへ回す
            if deck[0] == last:
                deck.append(deck.pop(0))
            # 残りを引き切ったら（読めない声しか残っていなければ）、混ぜた次の巡に移る
            candidates = rest + deck
            index, voice = self._first_readable(profile, candidates)
            remaining = rest[index + 1 :] if index < len(rest) else candidates[index + 1 :]
            _shuffle_rounds[key] = (remaining, candidates[index])
        return voice

    def _first_readable(self, profile: Profile, candidates: list[str]) -> tuple[int, Profile]:
        """候補を順に読み、最初に読めた声とその位置。全員を読むと読み上げのたびにメンバーの数だけ読む。"""
        for index, member in enumerate(candidates):
            voice = self.get(member)
            if voice is not None and not voice.is_random:
                return index, voice
        raise ProfileNotFound(profile.id)

    def tone(self, profile_id: str) -> history.VersionedText:
        """その声の要約の口調（版つき）。空なら既定の口調で読む。"""
        return history.VersionedText(self.root / profile_id / TONE_FILENAME, "tone")

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
        profile_id, now = self._new_directory()
        directory = self.root / profile_id
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

    def create_random(self, *, name: str, members: list[str], order: str = "random") -> Profile:
        """メンバーから毎回1つを選ぶ声を作る。"""
        with _write_lock:
            members = self._check_members(members)
            _check_order(order)
            profile_id, now = self._new_directory()
            meta = {
                "name": name,
                "source": RANDOM_SOURCE,
                "members": members,
                "order": order,
                "created_at": now.isoformat(),
            }
            _write_meta(self.root / profile_id, meta)
            return self.require(profile_id)

    def _new_directory(self) -> tuple[str, datetime]:
        """新しい ID のディレクトリを作り、ID と作った時刻を返す。"""
        now = datetime.now(UTC)
        profile_id = f"p-{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(2)}"
        (self.root / profile_id).mkdir(parents=True)
        return profile_id, now

    def _check_members(self, members: list[str]) -> list[str]:
        """重なりを除いて、ランダムでない声だけが MIN_MEMBERS 以上あることを確かめる。"""
        unique = list(dict.fromkeys(members))
        for member in unique:
            # ランダムの声を入れ子にすると、消したときの扱いや循環を考えることになる
            if self.require(member).is_random:
                raise ProfileRejected("ランダムの声はメンバーにできません")
        if len(unique) < MIN_MEMBERS:
            raise ProfileRejected(f"メンバーは {MIN_MEMBERS} つ以上選びます")
        return unique

    def activate(self, profile_id: str) -> Profile:
        with _write_lock:
            profile = self.require(profile_id)
            atomic_write(self.root / ACTIVE_FILENAME, profile.id + "\n")
        return profile

    def update(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        caption: str | None = None,
        tone: str | None = None,
        members: list[str] | None = None,
        order: str | None = None,
    ) -> Profile:
        """名前・caption・口調・メンバー・選び方を書き換える。名前以外は次の読み上げから効き、caption と口調は版を残す。

        ランダムの声は名前とメンバーと選び方だけ、ほかの声はそれ以外だけを書ける
        """
        with _write_lock:
            profile = self.require(profile_id)
            if profile.is_random and (caption is not None or tone is not None):
                raise ProfileRejected("ランダムの声は caption と口調を持ちません")
            if not profile.is_random and (members is not None or order is not None):
                raise ProfileRejected("メンバーと選び方を持つのはランダムの声だけです")
            if members is not None:
                members = self._check_members(members)
            if order is not None:
                _check_order(order)
            directory = self.root / profile.id
            meta = json.loads((directory / META_FILENAME).read_text(encoding="utf-8"))
            if name is not None:
                meta["name"] = name
            if caption is not None:
                meta["caption"] = caption
            if members is not None:
                meta["members"] = members
            if order is not None:
                meta["order"] = order
            _write_meta(directory, meta)
            if caption is not None:
                history.snapshot("caption", directory / history.HISTORY_DIRNAME, caption)
            if tone is not None:
                self.tone(profile.id).write(tone)
        return dataclasses.replace(
            profile,
            name=profile.name if name is None else name,
            caption=profile.caption if caption is None else caption,
            tone=profile.tone if tone is None else tone,
            members=profile.members if members is None else tuple(members),
            order=profile.order if order is None else order,
        )

    def delete(self, profile_id: str) -> None:
        with _write_lock:
            profile = self.require(profile_id)
            active = self.active()
            if active is not None and active.id == profile.id:
                raise ProfileInUse("使用中のプロファイルは削除できません")
            # 消すと、ランダムの声が選べる声が黙って減る
            holders = [p.name for p in self.all() if profile.id in p.members]
            if holders:
                raise ProfileInUse(f"ランダムの声（{'、'.join(holders)}）から外してから削除します")
            shutil.rmtree(self.root / profile.id, ignore_errors=True)

    def caption_versions(self, profile_id: str) -> list[dict[str, str]]:
        profile = self.require(profile_id)
        return history.list_versions("caption", self._history_dir(profile))

    def restore_caption(self, profile_id: str, version: str) -> Profile:
        profile = self.require(profile_id)
        content = history.read_version("caption", self._history_dir(profile), version)
        # 復元も履歴に1件増える。「その版が再び動き出した」記録として正しい
        return self.update(profile.id, caption=content)

    def tone_versions(self, profile_id: str) -> list[dict[str, str]]:
        return self.tone(self.require(profile_id).id).versions()

    def restore_tone(self, profile_id: str, version: str) -> Profile:
        profile = self.require(profile_id)
        content = history.read_version("tone", self._history_dir(profile), version)
        # 復元も履歴に1件増える
        return self.update(profile.id, tone=content)

    def _history_dir(self, profile: Profile) -> Path:
        return self.root / profile.id / history.HISTORY_DIRNAME


def _check_order(order: str) -> None:
    if order not in ORDERS:
        raise ProfileRejected(f"選び方は {'・'.join(ORDERS)} のどれかです")


def _write_meta(directory: Path, meta: dict) -> None:
    atomic_write(directory / META_FILENAME, json.dumps(meta, ensure_ascii=False, indent=2))
