"""読み上げの記録（コーパス）・レビュー・アーカイブと、合成した音声の HTTP API。

studio のレビュー画面が使う。書くのは常駐サーバーだけで、studio の画面はここを呼ぶ。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, StrictBool, StringConstraints

from . import corpus, history
from .api_errors import error_doc as _error
from .data_api import Ok
from .prompt import PromptStore
from .tts import AudioNotFound, resolve_data_audio

MessageId = Annotated[str, StringConstraints(min_length=1)]
_AUDIO_NOT_FOUND: dict[int | str, dict] = {
    200: {"content": {"audio/wav": {}}},
    404: _error("`audio_not_found`"),
}


class CorpusItem(BaseModel):
    # フックが書いた行をそのまま返すので、欠けた項目は空にして1行で全体を落とさない
    ts: str = Field(default="", description="読み上げた時刻（フックが書いたローカル時刻）")
    message_id: str = Field(default="", description="読み上げの ID")
    input: str = Field(default="", description="要約した返事")
    summary: str = Field(default="", description="読み上げた要約")
    audio_path: str | None = Field(
        default=None, description="合成した音声（データの置き場所からの相対パス）"
    )
    verdict: Literal["good", "bad"] | None = Field(description="レビュー。未レビューは null")
    ideal: str | None = Field(description="👎 に添えた理想の出力")
    archived: bool = Field(description="アーカイブ済みか")
    stale: bool = Field(description="いまのプロンプトより前の版で作った要約か")


class CorpusResponse(BaseModel):
    total: int = Field(description="全件の数")
    items: list[CorpusItem] = Field(description="新しい順の1ページ")
    prompt_changed_at: str | None = Field(
        description="いまのプロンプトが動き出した時刻（ISO 8601、UTC）。分からなければ null"
    )


class ReviewBody(BaseModel):
    message_id: MessageId = Field(description="読み上げの ID")
    verdict: Literal["good", "bad", "none"] = Field(description="none は取り消し")
    ideal: str = Field(default="", description="理想の出力。verdict が bad のときだけ残す")


class ArchiveBody(BaseModel):
    message_id: MessageId = Field(description="読み上げの ID")
    archived: StrictBool = Field(description="false は解除")


class BulkArchiveBody(BaseModel):
    message_ids: list[MessageId] = Field(min_length=1, description="読み上げの ID")
    archived: StrictBool = Field(description="false は解除")


class BulkResult(Ok):
    count: int = Field(description="書いた件数")


def corpus_router(data_root: Path) -> APIRouter:
    router = APIRouter()
    prompt = PromptStore(data_root)
    reviews_path = data_root / corpus.REVIEWS_FILENAME
    archives_path = data_root / corpus.ARCHIVES_FILENAME

    @router.get("/corpus", summary="読み上げの記録", tags=["レビュー"])
    def list_corpus(
        limit: Annotated[int, Query(ge=0, description="1ページの件数")] = 50,
        offset: Annotated[int, Query(ge=0, description="飛ばす件数（新しい順）")] = 0,
    ) -> CorpusResponse:
        """新しい順に、レビュー・アーカイブ・旧プロンプトかどうかを添えて返す。

        絞り込みと検索は受けない（studio は全件をページに分けて読み、手元で絞る）。
        """
        page = corpus.corpus_page(data_root, limit, offset, prompt.current_since())
        return CorpusResponse.model_validate(page)

    @router.get(
        "/corpus/{message_id}/audio",
        summary="読み上げた音声",
        tags=["レビュー"],
        response_class=FileResponse,
        responses=_AUDIO_NOT_FOUND,
    )
    def corpus_audio(message_id: str):
        relative = corpus.find_audio_path(data_root, message_id)
        if relative is None:
            raise AudioNotFound(message_id)
        return FileResponse(resolve_data_audio(data_root, relative), media_type="audio/wav")

    @router.post("/reviews", summary="レビューする", tags=["レビュー"])
    def review(body: ReviewBody) -> Ok:
        """追記する（後の行が優先される）。`pairvoice eval --reviews` がケースに使う。"""
        record = {
            "ts": _now(),
            "message_id": body.message_id,
            "verdict": body.verdict,
            "ideal": body.ideal if body.verdict == "bad" else "",
        }
        corpus.append_jsonl(reviews_path, [record])
        return Ok()

    @router.post("/archives", summary="アーカイブする・解除する", tags=["レビュー"])
    def archive(body: ArchiveBody) -> Ok:
        """追記する（後の行が優先される）。アーカイブしたものは評価のケースから外れる。"""
        record = {"ts": _now(), "message_id": body.message_id, "archived": body.archived}
        corpus.append_jsonl(archives_path, [record])
        return Ok()

    @router.post("/archives/bulk", summary="まとめてアーカイブする・解除する", tags=["レビュー"])
    def archive_bulk(body: BulkArchiveBody) -> BulkResult:
        ts = _now()
        corpus.append_jsonl(
            archives_path,
            ({"ts": ts, "message_id": m, "archived": body.archived} for m in body.message_ids),
        )
        return BulkResult(count=len(body.message_ids))

    @router.get(
        "/audio",
        summary="合成した音声",
        tags=["声"],
        response_class=FileResponse,
        responses=_AUDIO_NOT_FOUND,
    )
    def audio(
        path: Annotated[
            str,
            Query(
                description="`/synthesize` が返した `relative_path`（データの置き場所からの相対パス）"
            ),
        ],
    ):
        return FileResponse(resolve_data_audio(data_root, path), media_type="audio/wav")

    return router


def _now() -> str:
    return history.iso_millis(datetime.now(UTC))
