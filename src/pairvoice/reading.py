"""音声合成が誤読しがちな語を読みに開く読み辞書（データの置き場所の dict.tsv）。

合成のたびに読むので、studio で保存した辞書が再起動なしに効く。置き換えは上から順に、
字句どおりに行う（studio のプレビュー、server.ts の applyDict と同じ結果になる）。
例えば「通り」を足すと「予定通り（どおり）」まで「とおり」に化けるので、広げすぎない。
"""

from __future__ import annotations

from pathlib import Path

DICT_FILENAME = "dict.tsv"


def load_dict(path: Path) -> list[tuple[str, str]]:
    """(置換元, 置換先) の列。3列目のメモは捨てる。無い・読めない辞書は空とみなす。"""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    rows = []
    # 区切るのは改行だけ（splitlines は U+2028 なども区切り、studio の parseDictTsv とずれる）。
    # 手で編集されて CRLF になっていても、行末の \r を置換先に混ぜない
    for line in content.split("\n"):
        line = line.removesuffix("\r")
        # タブを含まない行（空行・見出しのメモ等）は飛ばす
        if "\t" not in line:
            continue
        source, target = line.split("\t")[:2]
        # 空の置換元は全文字の間に置換先を差し込むので飛ばす
        if source:
            rows.append((source, target))
    return rows


def apply_dict(text: str, rows: list[tuple[str, str]]) -> str:
    for source, target in rows:
        text = text.replace(source, target)
    return text
