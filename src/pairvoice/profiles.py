"""声のプロファイル（参照音声 + caption の組）の読み書き。

データの置き場所の profiles/ に置き、常駐サーバーだけが書く（studio の画面は API を呼ぶ）。
合成のたびに active と profile.json を読むので、API で切り替えた・書き換えた版が
再起動なしに効く。

ランダムの声（source が random）は参照音声を持たず、ほかの声（メンバー）を束ねる。
メンバーにはランダムの声も入れられ（自分に戻る入れ子は断る）、入れ子をたどって行き着く
末端の声すべてから等しく選ぶ（フラット。入れ子の声の選び方は使わない）。使用中にも `voice` にもでき、
要約・合成のたびに読む声を1つ選ぶ（pick）。
選び方（order）がシャッフルなら、混ぜた順に末端の声の全員を一巡するまで同じ声を選ばない。
グループ（group）を渡すと、同じグループには前に選んだ声を選び続ける（groups.json に覚える）。
"""

from __future__ import annotations

import dataclasses
import json
import random
import re
import secrets
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import history
from .files import atomic_write

ACTIVE_FILENAME = "active"
# ランダムの声ごとの、グループに選んだ声。常駐サーバーを入れ替えるたびに再起動するので、
# メモリだけに持つと更新のたびに Claude Code のセッションの途中で声が変わる
GROUPS_FILENAME = "groups.json"
# これより長く使っていないグループは忘れる（終わったセッションで溜まり続けないように）
GROUP_RETENTION = timedelta(days=7)
# 同じ声を選び続けている間は、使った時刻をこれより細かく書き直さない（読み上げのたびに書かない）
GROUP_TOUCH_INTERVAL = timedelta(days=1)
META_FILENAME = "profile.json"
REFERENCE_FILENAME = "reference.wav"
# 素材を畳み込んだ声の、話者の表現（トークンの列、numpy の .npy）
SPEAKER_FILENAME = "speaker.npy"
PROFILES_DIRNAME = "profiles"
# 要約の口調（声ごとのものは声のディレクトリに、既定のものはデータの置き場所に置く）
TONE_FILENAME = "tone.txt"
# active や URL の ID でディレクトリの外へ出ないよう、読むときもこの形だけを受ける
_ID_PATTERN = re.compile(r"p-[0-9A-Za-z-]+")
# design = caption や合成した声から作った、upload = 手持ちの wav を取り込んだ、
# curate = 雑多な音声ファイルから選り分けた、auto / import = 初回に常駐サーバーが作った、
# random = ほかの声から毎回1つを選ぶ、mix = ほかの声の話者の表現を重みで混ぜた
RANDOM_SOURCE = "random"
MIX_SOURCE = "mix"
SOURCES = ("design", "upload", "curate", "auto", "import", RANDOM_SOURCE, MIX_SOURCE)
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
# groups.json を読んで書き戻す間を1本にする（重なると、片方が選んだ声が消える）
_groups_lock = threading.Lock()


@dataclass(frozen=True)
class MixVoice:
    """混ぜる材料にする声（プロファイルの名前か ID）。読み上げと同じ表現で混ぜる。"""

    key: str


@dataclass(frozen=True)
class SpeakerTokens:
    """素材を畳み込んだ声の話者の表現。write が .npy を書き、model はそれを作った合成モデルの名前。"""

    write: Callable[[Path], object]
    model: str


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
    # 素材を畳み込んだ声だけが持つ、話者の表現と、それを作った合成モデルの名前。表現は
    # そのモデルの空間のものなので、モデルが違えば使わず参照音声で読む
    speaker: Path | None = None
    speaker_model: str = ""
    # 混ぜた声の作り方（もとの声の ID と重み）。表現は speaker に写して持つので、もとの声を
    # 消しても読める。作り直すときの控え
    mix: tuple[tuple[str, float], ...] = ()

    def tokens_for(self, model: str) -> Path | None:
        """model で読むときに差し込む話者の表現。別のモデルで作った表現は使わない（空間が違う）。"""
        return self.speaker if self.speaker is not None and self.speaker_model == model else None

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
            # 混ぜた声も表現を持つが、素材から畳み込んだ声ではない
            "folded": self.speaker is not None and not self.mix,
            "mix": [{"id": voice, "weight": weight} for voice, weight in self.mix],
        }


def _read_mix(raw: object) -> tuple[tuple[str, float], ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        (str(part["id"]), float(part["weight"]))
        for part in raw
        if isinstance(part, dict) and isinstance(part.get("weight"), int | float) and "id" in part
    )


def _read_groups(path: Path) -> dict[str, dict[str, str]]:
    """groups.json（`<ランダムの声の ID>/<グループ>` → 選んだ声と使った時刻）。無い・壊れているなら空。"""
    try:
        groups = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return groups if isinstance(groups, dict) else {}


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
        speaker = directory / SPEAKER_FILENAME
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
            speaker=speaker if speaker.is_file() else None,
            speaker_model=str(meta.get("speaker_model", "")),
            mix=_read_mix(meta.get("mix")),
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

    def resolve(self, key: str | None, group: str | None = None) -> Profile | None:
        """API で選ばれた声で読む声。省けば使用中の声（まだ無ければ None）、名前か ID が無ければ ProfileNotFound。

        ランダムの声なら、メンバーから1つを選んで返す（group は pick と同じ）
        """
        profile = self.active() if key is None else self.find(key)
        if profile is None:
            if key is None:
                return None
            raise ProfileNotFound(key)
        return self.pick(profile, group)

    def pick(self, profile: Profile, group: str | None = None) -> Profile:
        """読む声。ランダムの声なら末端の声から1つを選び、読める声が無ければ ProfileNotFound。

        入れ子のランダムの声は1人と数えず、その末端の声を並べて選ぶ（声の数に比例して当たる）。
        group を渡すと、そのグループに前に選んだ声がまだ読める末端の声ならそれを返す。
        初めてのグループにだけ新しく選ぶので、シャッフルならグループ同士が別の声になる
        """
        if not profile.is_random:
            return profile
        leaves = {d.id: d for d in self._descendants(profile) if not d.is_random}
        if not leaves:
            raise ProfileNotFound(profile.id)
        if group is None:
            return self._draw(profile, leaves)
        path = self.root / GROUPS_FILENAME
        # ID は / を含まないので、グループに / があっても取り違えない
        key = f"{profile.id}/{group}"
        now = datetime.now(UTC)
        with _groups_lock:
            groups = _read_groups(path)
            entry = groups.get(key) or {}
            chosen = leaves.get(entry.get("voice"))
            if (
                chosen is not None
                and now - datetime.fromisoformat(entry["used_at"]) < GROUP_TOUCH_INTERVAL
            ):
                return chosen
            voice = chosen or self._draw(profile, leaves)
            groups[key] = {"voice": voice.id, "used_at": now.isoformat()}
            fresh = {
                k: e
                for k, e in groups.items()
                if now - datetime.fromisoformat(e["used_at"]) <= GROUP_RETENTION
            }
            atomic_write(path, json.dumps(fresh, ensure_ascii=False))
        return voice

    def _draw(self, profile: Profile, leaves: dict[str, Profile]) -> Profile:
        """選び方（order）に従って、末端の声から新しく1つを選ぶ。"""
        deck = random.sample(list(leaves), len(leaves))
        if profile.order != "shuffle":
            return leaves[deck[0]]
        key = self.root / profile.id
        with _shuffle_lock:
            rest, last = _shuffle_rounds.get(key, ([], None))
            # 外した声・読めなくなった声は残りから除く。足した声は次の巡から入る
            rest = [m for m in rest if m in leaves]
            if not rest:
                # 巡の変わり目で同じ声が続かないよう、前の巡の最後を後ろへ回す
                if deck[0] == last:
                    deck.append(deck.pop(0))
                rest = deck
            _shuffle_rounds[key] = (rest[1:], rest[0])
        return leaves[rest[0]]

    def _descendants(self, profile: Profile) -> list[Profile]:
        """メンバーをたどって行き着く、読める声すべて（ランダムの声も含む）。

        メンバーの並び順に深さ優先でたどる。何通りの入れ子から行き着いても1つと数え、手で書き換えた輪でも止まる
        """
        found: dict[str, Profile] = {}
        stack = list(reversed(profile.members))
        while stack:
            member = stack.pop()
            if member in found or (voice := self.get(member)) is None:
                continue
            found[member] = voice
            stack.extend(reversed(voice.members))
        return list(found.values())

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
        speaker: SpeakerTokens | None = None,
        mix: Sequence[tuple[str, float]] = (),
    ) -> Profile:
        """プロファイルを作る。参照音声は write_reference が書く。speaker は畳み込んだ・混ぜた声の
        表現で、mix は混ぜた声の作り方。

        使用中のプロファイルがまだ無ければ、作ったものを使用中にする。放っておくと次の
        読み上げで既定の声が自動で作られ、作った声が使われない
        """
        profile_id, now = self._new_directory()
        directory = self.root / profile_id
        try:
            write_reference(directory / REFERENCE_FILENAME)
            if speaker is not None:
                speaker.write(directory / SPEAKER_FILENAME)
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
        if speaker is not None:
            meta["speaker_model"] = speaker.model
        if mix:
            meta["mix"] = [{"id": voice, "weight": weight} for voice, weight in mix]
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
            speaker=None if speaker is None else directory / SPEAKER_FILENAME,
            speaker_model="" if speaker is None else speaker.model,
            mix=tuple(mix),
        )

    def create_random(self, *, name: str, members: list[str], order: str = "random") -> Profile:
        """メンバーから毎回1つを選ぶ声を作る。"""
        with _write_lock:
            members = self._check_members(members, None)
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

    def _check_members(self, members: list[str], owner: str | None) -> list[str]:
        """重なりを除いて、MIN_MEMBERS 以上あり、どれも owner（書き換えるランダムの声）に戻らないことを確かめる。"""
        unique = list(dict.fromkeys(members))
        for member in unique:
            # 輪になると、選ぶたびに同じ声の間を回り続ける
            voice = self.require(member)
            if owner is not None and (
                voice.id == owner or any(d.id == owner for d in self._descendants(voice))
            ):
                raise ProfileRejected("自分を含むランダムの声はメンバーにできません")
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
                members = self._check_members(members, profile.id)
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
