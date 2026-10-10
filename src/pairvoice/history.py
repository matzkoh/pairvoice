"""版の履歴（要約のプロンプト・口調と、声の caption）。

<dir>/<kind>-<ISO 時刻の : と . を - にしたもの>.txt に1版ずつ置く。名前の時刻から
「いま動いている版がいつ動き出したか」を読む（VersionedText.current_since）。

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


def iso_millis(at: datetime) -> str:
    """JavaScript の toISOString() と同じ、ミリ秒までの UTC（studio が Date で読む）。"""
    utc = at.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def snapshot(kind: str, directory: Path, content: str) -> None:
    # ファイル名に使えるよう、: と . を - に潰す
    stamp = iso_millis(datetime.now(UTC)).replace(":", "-").replace(".", "-")
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


def version_time(kind: str, name: str) -> datetime | None:
    """版の名前に付けた時刻。形の違う名前は None。"""
    match = re.fullmatch(
        rf"{kind}-(\d{{4}}-\d{{2}}-\d{{2}})T(\d{{2}})-(\d{{2}})-(\d{{2}})-(\d{{3}})Z\.txt", name
    )
    if match is None:
        return None
    day, hour, minute, second, milli = match.groups()
    try:
        return datetime.fromisoformat(f"{day}T{hour}:{minute}:{second}.{milli}+00:00")
    except ValueError:
        return None


def read_version(kind: str, directory: Path, name: str) -> str:
    # 名前の形を絞るので / や .. を含められず、置き場所の外は指せない
    if not re.fullmatch(rf"{kind}-[0-9TZ-]+\.txt", name):
        raise InvalidVersion(name)
    try:
        return (directory / name).read_text(encoding="utf-8")
    except OSError as missing:
        raise VersionNotFound(name) from missing


class VersionedText:
    """書くたびに版を残すテキスト（プロンプトと口調）。"""

    def __init__(self, path: Path, kind: str) -> None:
        self.path = path
        # 版はファイルの隣の history/ に、kind を頭に付けて置く
        self.history_dir = path.parent / HISTORY_DIRNAME
        self.kind = kind

    def read(self) -> str:
        """まだ無ければ空。"""
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write(self, text: str) -> None:
        """書き換える経路（編集・復元）は必ずここを通し、版を残す。

        版を残さない経路が1つでもあると「気に入らなかったので戻す」ができなくなる。
        """
        atomic_write(self.path, text)
        snapshot(self.kind, self.history_dir, text)

    def versions(self) -> list[dict[str, str]]:
        return list_versions(self.kind, self.history_dir)

    def restore(self, name: str) -> None:
        # 復元も履歴に1件増える。「その版が再び動き出した」記録として正しい
        self.write(read_version(self.kind, self.history_dir, name))

    def current_since(self) -> datetime | None:
        """いま動いている版が、その中身で動き出した時刻。

        最新の版の時刻を使ってはいけない。版は書くたびに1件増えるので、中身を変えずに
        保存し直しただけ・同じ内容の版に戻しただけでも時刻が進み、直前まで現行だった
        読み上げが全部「旧プロンプト」に落ちる。そこで新しい順にたどり、中身が現行と
        一致する版が続く限り遡って、一致が途切れた次を境界にする。

        ファイルが無い、版が無い、最新の版とも中身が違う（API を通さず書き換えた）
        ときは None。いつから動いているか分からないのに境界を引くと、現行の読み上げを
        「旧」と誤って隠す。
        """
        try:
            current = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        since = None
        for version in self.versions():
            at = version_time(self.kind, version["name"])
            if at is None:
                continue
            try:
                content = (self.history_dir / version["name"]).read_text(encoding="utf-8")
            except OSError:
                break
            if content != current:
                break
            since = at
        return since
