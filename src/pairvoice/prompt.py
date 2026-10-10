"""要約のプロンプト（データの置き場所の prompt.txt）。フックが読み上げのたびに読む。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import history
from .files import atomic_write

PROMPT_FILENAME = "prompt.txt"


class PromptStore:
    def __init__(self, data_root: Path) -> None:
        self.path = data_root / PROMPT_FILENAME
        self.history_dir = data_root / history.HISTORY_DIRNAME

    def read(self) -> str:
        """まだ無ければ空。"""
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write(self, text: str) -> None:
        """prompt.txt を書き換える経路（編集・復元）は必ずここを通し、版を残す。

        版を残さない経路が1つでもあると「気に入らなかったので戻す」ができなくなる。
        """
        atomic_write(self.path, text)
        history.snapshot("prompt", self.history_dir, text)

    def versions(self) -> list[dict[str, str]]:
        return history.list_versions("prompt", self.history_dir)

    def restore(self, name: str) -> None:
        # 復元も履歴に1件増える。「その版が再び動き出した」記録として正しい
        self.write(history.read_version("prompt", self.history_dir, name))

    def current_since(self) -> datetime | None:
        """いま動いているプロンプトが、その中身で動き出した時刻。

        最新の版の時刻を使ってはいけない。版は書くたびに1件増えるので、中身を変えずに
        保存し直しただけ・同じ内容の版に戻しただけでも時刻が進み、直前まで現行だった
        読み上げが全部「旧プロンプト」に落ちる。そこで新しい順にたどり、中身が現行と
        一致する版が続く限り遡って、一致が途切れた次を境界にする。

        prompt.txt が無い、版が無い、最新の版とも中身が違う（API を通さず書き換えた）
        ときは None。いつから動いているか分からないのに境界を引くと、現行の読み上げを
        「旧」と誤って隠す。
        """
        try:
            current = self.path.read_text(encoding="utf-8")
        except OSError:
            return None
        since = None
        for version in self.versions():
            at = history.version_time("prompt", version["name"])
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
