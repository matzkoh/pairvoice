"""音声合成が誤読しがちな語を読みに開く読み辞書（データの置き場所の dict.tsv）。

合成のたびに読むので、studio で保存した辞書が再起動なしに効く。置き換えは上から順に、
字句どおりに行う（studio のプレビューも POST /dict/test でこれを使う）。
例えば「通り」を足すと「予定通り（どおり）」まで「とおり」に化けるので、広げすぎない。
"""

from __future__ import annotations

from pathlib import Path

from .files import atomic_write

DICT_FILENAME = "dict.tsv"
# 値に混ざると列と行がずれ、その行から後ろの置換が壊れる
TSV_CONTROL = frozenset("\t\n\r")


def _lines(path: Path) -> list[str]:
    """空でない行。無い・読めない辞書は空とみなす。"""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    # 区切るのは改行だけ（splitlines は U+2028 なども区切り、studio のプレビューとずれる）。
    # 手で編集されて CRLF になっていても、行末の \r を置換先に混ぜない
    return [line for line in (raw.removesuffix("\r") for raw in content.split("\n")) if line]


def load_dict(path: Path) -> list[tuple[str, str]]:
    """(置換元, 置換先) の列。3列目のメモは捨てる。"""
    rows = []
    for line in _lines(path):
        # タブを含まない行（見出しのメモ等）は飛ばす
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


def read_rows(path: Path) -> list[dict[str, str]]:
    """編集用に全行を from / to / memo で返す。置換に使わない行（タブの無い行）も残す。"""
    rows = []
    for line in _lines(path):
        source, target, memo = [*line.split("\t"), "", ""][:3]
        rows.append({"from": source, "to": target, "memo": memo})
    return rows


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    """全行を置き換える。値の検査（空の from、タブ・改行）は呼ぶ側が済ませる。"""
    lines = "".join(f"{r['from']}\t{r['to']}\t{r.get('memo', '')}\n" for r in rows)
    atomic_write(path, lines)
