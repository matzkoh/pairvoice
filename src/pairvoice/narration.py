"""フックの読み上げ（/narrate）。返事を要約し、要約の口調の声で鳴らし、corpus.jsonl に記録する。

フックは返事1つにつきこれを1回叩くだけにする。要約から合成への受け渡し（ランダムの声で
口調と声をそろえる）と記録をフックに持たせると、常駐サーバーと別々に更新されるフックの
側で食い違いが起きる。
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .corpus import CORPUS_FILENAME, append_jsonl
from .lifecycle import Engine
from .prompt import SummaryPrompt

# 要約が日本語にならなかったときに試す回数（初回を含む）
SUMMARY_ATTEMPTS = 3

# ひらがな・カタカナ（半角を含む）・漢字
_JAPANESE = re.compile(
    "[\u3040-\u30ff\u31f0-\u31ff\uff66-\uff9f\u3005\u3007"
    "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0003134f]"
)
_TAG = re.compile(r"</?[A-Za-z][^<>]*>")


class NothingToSay(Exception):
    """読み上げる中身が無い（閉じタグやコードフェンスだけの断片）。"""


class NoJapaneseSummary(Exception):
    """何度要約させても日本語にならなかった。"""


def has_speakable_content(text: str) -> bool:
    """MessageDisplay の返事には、閉じタグだけの断片（`</invoke>` など）やコードフェンスだけの
    行が単独で流れてくる。要約させると「入力がありません」を読み上げるか、無関係な要約を
    幻覚するので、タグとバッククォートを落として文字が残るものだけを読む。"""
    stripped = _TAG.sub("", text).replace("`", "")
    return any(char.isalnum() for char in stripped)


def contains_japanese(text: str) -> bool:
    return _JAPANESE.search(text) is not None


class NarrateResponse(BaseModel):
    summary: str = Field(description="読み上げた要約（読み辞書をかける前）")
    voice: str | None = Field(
        description="読んだ声（プロファイル）の ID。ランダムの声なら選んだメンバー。声がまだ無いときは null"
    )
    relative_path: str = Field(description="合成した wav のデータの置き場所からの相対パス")
    duration: float = Field(description="長さ（秒）")


async def narrate(
    engine: Engine,
    prompt: SummaryPrompt,
    data_root: Path,
    *,
    text: str,
    message_id: str,
    voice: str | None = None,
    style: str | None = None,
    wait_download: bool = True,
) -> NarrateResponse:
    """ミュート中は要約もしない。待っている間に後発の読み上げが来たら、要約の前に捨てる。"""
    if not has_speakable_content(text):
        raise NothingToSay
    system, profile = prompt.compose_summary(voice)
    for _ in range(SUMMARY_ATTEMPTS):
        summary = await engine.summarize(
            system, text, respect_mute=True, droppable=True, wait_download=wait_download
        )
        if contains_japanese(summary):
            break
    else:
        raise NoJapaneseSummary
    # ランダムの声は要約と合成で別々に選ぶので、要約が口調を使った声を名指しして読む
    used_voice = profile.id if profile is not None else None
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    relative_path = ""
    try:
        result = await engine.speak(
            summary, profile_id=used_voice or voice, wait_download=wait_download, style=style
        )
        relative_path = result.relative_path
    finally:
        # 鳴らせなくても、入力と要約の組はプロンプトの改善に使える
        record = {
            "ts": ts,
            "message_id": message_id,
            "input": text,
            "summary": summary,
            "audio_path": relative_path,
            **({} if used_voice is None else {"voice": used_voice}),
        }
        await asyncio.to_thread(append_jsonl, data_root / CORPUS_FILENAME, [record])
    return NarrateResponse(
        summary=summary,
        voice=used_voice,
        relative_path=relative_path,
        duration=result.duration,
    )
