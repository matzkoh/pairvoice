"""API のエラー応答。どれも {"error": コード, "detail": 補足} の形にそろえる。

ドメインの例外は、どのエンドポイントから起きても同じ応答にする。対応はこの表1か所に置き、
エンドポイントは例外を捕まえずにストアを呼ぶだけにする。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .history import InvalidVersion, VersionNotFound
from .profiles import ProfileInUse, ProfileNotFound, TakeRejected
from .styles import StyleInvalid, StyleNotFound, StyleRejected
from .tts import AudioNotFound
from .wav import WavFormatError


class ErrorResponse(BaseModel):
    error: str = Field(description="エラーの種類")
    detail: str | None = Field(default=None, description="補足（無いこともある）")


def error_doc(description: str) -> dict:
    """/docs に載せるエラーの説明。"""
    return {"model": ErrorResponse, "description": description}


def fail(status: int, error: str, detail: str | None = None) -> JSONResponse:
    content = {"error": error} if detail is None else {"error": error, "detail": detail}
    return JSONResponse(status_code=status, content=content)


# 例外 → (HTTP の状態, error のコード, 例外の文言を detail に載せるか)
_ERRORS: dict[type[Exception], tuple[int, str, bool]] = {
    StyleNotFound: (404, "style_not_found", False),
    StyleInvalid: (500, "style_invalid", True),
    StyleRejected: (400, "invalid_style", True),
    ProfileNotFound: (404, "profile_not_found", False),
    ProfileInUse: (409, "profile_in_use", True),
    AudioNotFound: (404, "audio_not_found", False),
    InvalidVersion: (400, "invalid_version", False),
    VersionNotFound: (404, "version_not_found", False),
    WavFormatError: (400, "invalid_take", True),
    TakeRejected: (400, "invalid_take", True),
}


def install_error_handlers(app: FastAPI) -> None:
    for exception, (status, code, with_detail) in _ERRORS.items():

        async def handle(
            request: Request, error: Exception, status=status, code=code, with_detail=with_detail
        ):
            return fail(status, code, str(error) if with_detail else None)

        app.add_exception_handler(exception, handle)
