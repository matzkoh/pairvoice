"""要約のプロンプト（データの置き場所の prompt.txt）。フックが読み上げのたびに読む。"""

from __future__ import annotations

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
