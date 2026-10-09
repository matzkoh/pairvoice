"""話し方のスタイル（名前付きの caption + sampler の組）の読み込み。

データの置き場所の styles.json に置き、API（studio の画面も）で書いて合成のたびに読む。/speak・/synthesize の
style で名前を指定すると、そのリクエストだけこの caption と sampler で合成する。
合成のたびに読むので、studio で直した版が再起動なしに効く。

caption は省ける（null）。省いたスタイルは声のプロファイルの caption のまま読み、
sampler だけを変える（速さだけ変える、など）。
"""

from __future__ import annotations

import dataclasses
import json
import typing
from dataclasses import dataclass
from pathlib import Path

from .config import SamplerConfig, _coerce
from .files import atomic_write

STYLES_FILENAME = "styles.json"
# 合成のたびに読み直すので、型注釈の解決は1回で済ませる
_SAMPLER_HINTS = typing.get_type_hints(SamplerConfig)


@dataclass(frozen=True)
class Style:
    name: str
    caption: str | None
    sampler: dict[str, object]


class StyleNotFound(Exception):
    """指定された名前のスタイルが無い（studio で消された・名前の打ち間違い）。"""


class StyleInvalid(Exception):
    """styles.json が読めない、または形が違う。手で書き換えたときに起きる。"""


class StyleRejected(ValueError):
    """保存しようとしたスタイルの形が違う。styles.json は書き換えない。"""


def _parse_sampler(name: str, raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StyleInvalid(f"スタイル「{name}」の sampler はオブジェクトにしてください")
    unknown = raw.keys() - _SAMPLER_HINTS.keys()
    if unknown:
        # 名前がずれた値は generate() に黙って捨てられ、指定したのに効かないことになる
        raise StyleInvalid(
            f"スタイル「{name}」の sampler に知らない項目があります: {sorted(unknown)}"
        )
    try:
        return {
            key: _coerce(f"スタイル「{name}」の sampler.{key}", value, _SAMPLER_HINTS[key])
            for key, value in raw.items()
        }
    except ValueError as error:
        raise StyleInvalid(str(error)) from error


def _parse(raw: object) -> list[Style]:
    entries = raw.get("styles") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise StyleInvalid('styles.json は {"styles": [...]} の形にしてください')
    styles = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise StyleInvalid("スタイルには文字列の name が要ります")
        name = entry["name"]
        caption = entry.get("caption")
        if caption is not None and not isinstance(caption, str):
            raise StyleInvalid(f"スタイル「{name}」の caption は文字列か null にしてください")
        styles.append(
            Style(name=name, caption=caption, sampler=_parse_sampler(name, entry.get("sampler")))
        )
    return styles


class StyleStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def all(self) -> list[Style]:
        """すべてのスタイル。ファイルが無ければ空。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as error:
            raise StyleInvalid(f"styles.json を読めません: {error}") from error
        try:
            raw = json.loads(text)
        except ValueError as error:
            raise StyleInvalid(f"styles.json が JSON として壊れています: {error}") from error
        return _parse(raw)

    def save(self, entries: list[dict]) -> list[Style]:
        """全件を置き換える。1件でも崩れていれば StyleRejected で、何も書かない。"""
        try:
            parsed = _parse({"styles": entries})
        except StyleInvalid as invalid:
            raise StyleRejected(str(invalid)) from invalid
        styles = [dataclasses.replace(style, name=style.name.strip()) for style in parsed]
        seen: set[str] = set()
        for style in styles:
            if not style.name:
                raise StyleRejected("スタイルの name を空にはできません")
            # /speak は名前で引くので、重なると後ろのスタイルに届かない
            if style.name in seen:
                raise StyleRejected(f"スタイル「{style.name}」が重なっています")
            seen.add(style.name)
        body = {"styles": [dataclasses.asdict(style) for style in styles]}
        atomic_write(self.path, json.dumps(body, ensure_ascii=False, indent=2) + "\n")
        return styles

    def get(self, name: str) -> Style:
        for style in self.all():
            if style.name == name:
                return style
        raise StyleNotFound(name)
