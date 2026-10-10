"""データの置き場所の CRUD（プロンプト・読み辞書・スタイル・声）の HTTP API。

書くのは常駐サーバーだけで、studio はここを中継する。ファイルを読み書きするので、
エンドポイントは def にしてイベントループの外（スレッドプール）で動かす。
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from . import reading
from .api_errors import error_doc as _error
from .api_errors import fail as _fail
from .profiles import PROFILES_DIRNAME, ProfileStore, TakeRejected
from .prompt import PromptStore
from .styles import STYLES_FILENAME, StyleStore
from .tts import ANCHOR_TEXT, SpeechResult, resolve_data_audio
from .wav import concat_wavs, has_wav_header

# 手持ちの wav を取り込むときの上限。数十秒の参照音声で足りる
UPLOAD_MAX_BYTES = 50 * 1024 * 1024
# テイクをつなぐときの数の上限と、間に挟む無音
MAX_TAKES = 8
TAKE_GAP_SECONDS = 0.3
# 伸ばすときの合成の段数（モデル既定は 40）。参照音声は一度しか作らないので丁寧に作る。
# studio の MASTER_STEPS と同じ値にしておく
MASTER_STEPS = 80
# 伸ばすときにテイクの声で読ませる文。Irodori-TTS は同じ話者の短い発話を合わせて 30 秒ほどの
# 参照音声を勧めるので、テイクにこの3つ（各 6〜7 秒）を足してつなぐ。studio の
# REFERENCE_TEXTS（web/src/features/profiles/mixSynth.ts）と同じ文にしておく
# - 読み上げるのはエージェントの作業の要約なので、落ち着いた説明調を軸にし、問いかけと
#   軽い相づちで抑揚に幅を持たせる。参照音声の話し方は複製した声に移るので、強い感情は入れない
# - 拗音（しゅ・ちょ・じゅ）、促音、撥音、長音、濁音・半濁音、カタカナ語と数を一通り含める
REFERENCE_TEXTS = (
    "ビルドとテストはすべて通りました。変更は三つのファイルにまとまっていて、再起動も済んでいます。",
    "ひとつ確認させてください。この設定は、来週のアップデートまでに切り替えておけば間に合いますか？",
    "ちょっと待ってくださいね。原因はわかったので、じゅうぶん直せそうです。順番に片づけましょう。",
)


class Synthesize(Protocol):
    """Engine.synthesize のうち、声を作る・伸ばすのに使う引数。"""

    async def __call__(
        self,
        text: str,
        caption: str | None = None,
        sampler: dict | None = None,
        design: bool = False,
        *,
        mix: list[tuple[str, float]] | None = None,
    ) -> SpeechResult: ...


_VERSION_ERRORS: dict[int | str, dict] = {
    400: _error("`invalid_version`（版の名前の形が違う）"),
    404: _error("`version_not_found`"),
}
_PROFILE_NOT_FOUND: dict[int | str, dict] = {404: _error("`profile_not_found`")}

# 声の名前と caption は前後の空白を落として保存する。名前は空にさせない
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProfileName = NonBlank
Caption = Annotated[str, StringConstraints(strip_whitespace=True)]


class Ok(BaseModel):
    ok: bool = Field(default=True, description="常に true")


class PromptResponse(BaseModel):
    text: str = Field(description="まだ無ければ空")


class PromptBody(BaseModel):
    text: str = Field(description="要約のシステムプロンプト。フックが読み上げのたびに読む")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, text: str) -> str:
        if not text.strip():
            raise ValueError("text must not be blank")
        return text


class HistoryItem(BaseModel):
    name: str = Field(description="版の名前。復元のときに渡す")
    ts: str = Field(description="保存した時刻（表示用。日時としては読めない形）")


class HistoryResponse(BaseModel):
    items: list[HistoryItem] = Field(description="新しい順")


class RestoreBody(BaseModel):
    name: str = Field(description="復元する版の名前（履歴の `name`）")


class DictRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from", description="置換元。空にはできない")
    target: str = Field(alias="to", description="置換先（読み）")
    memo: str = Field(default="", description="メモ。置換には使わない")

    @field_validator("source")
    @classmethod
    def _source_not_blank(cls, value: str) -> str:
        # 空の置換元は全文字の間に置換先を差し込む
        if not value.strip():
            raise ValueError("from must not be empty")
        return value

    @field_validator("source", "target", "memo")
    @classmethod
    def _no_tsv_control(cls, value: str) -> str:
        if reading.TSV_CONTROL & set(value):
            raise ValueError("must not contain tabs or line breaks")
        return value


class DictBody(BaseModel):
    rows: list[DictRow] = Field(description="上から順に、字句どおりに置き換える")


class StyleEntry(BaseModel):
    name: str = Field(description="`/speak` と `/synthesize` の `style` に渡す名前")
    caption: str | None = Field(
        default=None, description="null ならプロファイルの caption のまま読む"
    )
    sampler: dict[str, int | float | str | bool] = Field(
        default_factory=dict, description="`/synthesize` の `sampler` と同じ項目"
    )


class StylesBody(BaseModel):
    items: list[StyleEntry] = Field(description="全件。名前は重ねられない")


class ProfileItem(BaseModel):
    id: str = Field(description="ID。`voice` に渡せる")
    name: str = Field(description="名前。`voice` に渡せる")
    caption: str = Field(description="話し方の指示。合成のたびに読む")
    source: Literal["design", "upload", "auto", "import"] = Field(description="作り方")
    created_at: str = Field(description="作った日時（ISO 8601）")


class ProfilesResponse(BaseModel):
    active: str | None = Field(description="使用中の声の ID")
    items: list[ProfileItem] = Field(description="新しく作った順")


class CreateProfileBody(BaseModel):
    name: ProfileName = Field(description="声の名前")
    caption: Caption = Field(default="", description="話し方の指示")
    takes: list[str] = Field(
        min_length=1,
        max_length=MAX_TAKES,
        description="`/synthesize` で作った wav（データの置き場所からの相対パス）。"
        "短い無音を挟んで1本の参照音声につなぐ",
    )
    extend: bool = Field(
        default=False,
        description="テイクの声で決まった文（3つ、計 20 秒ほど）も読ませ、テイクの後ろにつなぐ。"
        "短いテイク1本からでも安定した声になる。テイクが複数なら等分に混ぜた声で読む。"
        "合成するので数十秒かかる",
    )
    rng_seed: int | None = Field(
        default=None,
        description="`extend` で読ませるときの乱数の種。省くと config.toml の値",
    )


class DesignProfileBody(BaseModel):
    name: ProfileName = Field(description="声の名前")
    caption: NonBlank = Field(
        description="声と話し方の描写。声を作るのに使い、プロファイルにもそのまま入れる"
    )
    text: NonBlank = Field(
        default=ANCHOR_TEXT, description="最初に caption だけで読ませる文。声はこの1本で決まる"
    )
    rng_seed: int | None = Field(
        default=None,
        description="乱数の種。同じ caption・文・種なら同じ声になる。"
        "省くと config.toml の値（無ければ毎回違う声）",
    )


class ActiveBody(BaseModel):
    id: str = Field(description="使用中にする声の ID")


class PatchProfileBody(BaseModel):
    name: ProfileName | None = Field(default=None, description="声の名前")
    caption: Caption | None = Field(
        default=None, description="空文字は caption なしで読む。書くたびに版を残す"
    )

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.name is None and self.caption is None:
            raise ValueError("name or caption is required")
        return self


def data_router(data_root: Path, synthesize: Synthesize) -> APIRouter:
    router = APIRouter()
    prompt = PromptStore(data_root)
    dict_path = data_root / reading.DICT_FILENAME
    styles = StyleStore(data_root / STYLES_FILENAME)
    profiles = ProfileStore(data_root / PROFILES_DIRNAME)

    # ---- プロンプト ----

    @router.get("/prompt", summary="要約のプロンプト", tags=["プロンプト"])
    def get_prompt() -> PromptResponse:
        return PromptResponse(text=prompt.read())

    @router.put("/prompt", summary="要約のプロンプトを書き換える", tags=["プロンプト"])
    def put_prompt(body: PromptBody) -> Ok:
        """書いた内容を履歴の最新版として残す。"""
        prompt.write(body.text)
        return Ok()

    @router.get("/prompt/history", summary="プロンプトの履歴", tags=["プロンプト"])
    def prompt_history() -> HistoryResponse:
        return HistoryResponse.model_validate({"items": prompt.versions()})

    @router.post(
        "/prompt/restore",
        summary="プロンプトを履歴の版に戻す",
        tags=["プロンプト"],
        responses=_VERSION_ERRORS,
    )
    def restore_prompt(body: RestoreBody) -> Ok:
        prompt.restore(body.name)
        return Ok()

    # ---- 読み辞書 ----

    @router.get("/dict", summary="読み辞書", tags=["読み辞書"])
    def get_dict() -> dict:
        """合成の直前に、上から順に字句どおりに置き換える。"""
        return {"rows": reading.read_rows(dict_path)}

    @router.put("/dict", summary="読み辞書を置き換える", tags=["読み辞書"])
    def put_dict(body: DictBody) -> Ok:
        """全行を置き換える。次の合成から効く。"""
        reading.write_rows(dict_path, [row.model_dump(by_alias=True) for row in body.rows])
        return Ok()

    # ---- スタイル ----

    @router.get(
        "/styles",
        summary="スタイルの一覧",
        tags=["声"],
        responses={500: _error("`style_invalid`（styles.json が壊れている）")},
    )
    def get_styles() -> dict:
        """`/speak` と `/synthesize` の `style` に渡せるスタイル。`caption` が null のスタイルはプロファイルの caption のまま読む。"""
        return {"items": [dataclasses.asdict(s) for s in styles.all()]}

    @router.put(
        "/styles",
        summary="スタイルを置き換える",
        tags=["声"],
        responses={400: _error("`invalid_style`（名前の重なり・知らない sampler の項目など）")},
    )
    def put_styles(body: StylesBody) -> Ok:
        """全件を置き換える。次の合成から効く。"""
        styles.save([entry.model_dump() for entry in body.items])
        return Ok()

    # ---- 声 ----

    @router.get("/profiles", summary="声の一覧", tags=["声"])
    def list_profiles() -> ProfilesResponse:
        """`/speak` と `/synthesize` の `voice` に渡せる声（プロファイル）。"""
        items = sorted(profiles.all(), key=lambda p: p.created_at, reverse=True)
        active = profiles.active()
        return ProfilesResponse.model_validate(
            {
                "active": active.id if active is not None else None,
                "items": [p.describe() for p in items],
            }
        )

    def read_takes(relatives: list[str]) -> list[bytes]:
        profiles_dir = profiles.root.resolve()
        takes = []
        for relative in relatives:
            take = resolve_data_audio(data_root, relative)
            # 他の声の参照音声を取り込み元にさせない
            if take.is_relative_to(profiles_dir):
                raise TakeRejected(relative)
            takes.append(take.read_bytes())
        return takes

    def master_sampler(rng_seed: int | None) -> dict[str, object]:
        sampler: dict[str, object] = {"num_steps": MASTER_STEPS}
        if rng_seed is not None:
            sampler["rng_seed"] = rng_seed
        return sampler

    async def extend(relatives: list[str], caption: str, sampler: dict) -> list[bytes]:
        """テイクを等分に混ぜた声で REFERENCE_TEXTS を読ませる。"""
        mix = [(relative, 1.0) for relative in relatives]
        made = []
        # 1文ずつ頼む（まとめると Runner を塞ぎ、フックの読み上げが待たされる）
        for text in REFERENCE_TEXTS:
            result = await synthesize(text, caption=caption, sampler=sampler, mix=mix)
            made.append(await asyncio.to_thread(result.path.read_bytes))
        return made

    async def save_design(name: str, caption: str, takes: list[bytes]) -> ProfileItem:
        joined = concat_wavs(takes, TAKE_GAP_SECONDS)
        created = await asyncio.to_thread(
            profiles.create,
            name=name,
            caption=caption,
            source="design",
            write_reference=lambda path: path.write_bytes(joined),
        )
        return ProfileItem.model_validate(created.describe())

    @router.post(
        "/profiles",
        status_code=201,
        response_model=ProfileItem,
        summary="合成した wav から声を作る",
        tags=["声"],
        responses={
            400: _error("`invalid_take`（声の参照音声を指している・形式が揃わない）"),
            404: _error("`audio_not_found`"),
            503: _error("`model_load_failed`（`extend` の合成でモデルを読み込めない）"),
        },
    )
    async def create_profile(body: CreateProfileBody):
        """使用中の声がまだ無ければ、作った声を使用中にする。"""
        takes = await asyncio.to_thread(read_takes, body.takes)
        if body.extend:
            takes += await extend(body.takes, body.caption, master_sampler(body.rng_seed))
        return await save_design(body.name, body.caption, takes)

    @router.post(
        "/profiles/design",
        status_code=201,
        response_model=ProfileItem,
        summary="caption だけから声を作る",
        tags=["声"],
        responses={503: _error("`model_load_failed` など（モデルを使えない）")},
    )
    async def design_profile(body: DesignProfileBody):
        """caption だけで `text` を読ませて声を決め、`POST /profiles` の `extend` と同じく決まった文も読ませてつなぐ。

        合成を4回するので 40 秒ほどかかる。使用中の声がまだ無ければ、作った声を使用中にする。
        """
        sampler = master_sampler(body.rng_seed)
        first = await synthesize(body.text, caption=body.caption, sampler=sampler, design=True)
        takes = [await asyncio.to_thread(first.path.read_bytes)]
        takes += await extend([first.relative_path], body.caption, sampler)
        return await save_design(body.name, body.caption, takes)

    @router.post(
        "/profiles/upload",
        status_code=201,
        response_model=ProfileItem,
        summary="手持ちの wav から声を作る",
        tags=["声"],
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
        responses={
            400: _error("`invalid_audio`（wav でない）"),
            413: _error("`body_too_large`（50MB まで）"),
        },
    )
    async def upload_profile(
        request: Request,
        name: Annotated[ProfileName, Query(description="声の名前")],
        caption: Annotated[Caption, Query(description="話し方の指示")] = "",
    ):
        """本文に wav をそのまま送る。使用中の声がまだ無ければ、作った声を使用中にする。"""
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > UPLOAD_MAX_BYTES:
            return _fail(413, "body_too_large")
        audio = bytearray()
        # 申告の無い chunked も読みながら数える
        async for chunk in request.stream():
            audio += chunk
            if len(audio) > UPLOAD_MAX_BYTES:
                return _fail(413, "body_too_large")
        # 中身の妥当性は合成時に mlx-audio が判断する
        if not has_wav_header(audio):
            return _fail(400, "invalid_audio", "body must be a wav file")
        # 数十 MB を書くので、イベントループの外で行う
        created = await asyncio.to_thread(
            profiles.create,
            name=name,
            caption=caption,
            source="upload",
            write_reference=lambda path: path.write_bytes(audio),
        )
        return ProfileItem.model_validate(created.describe())

    @router.put(
        "/profiles/active",
        summary="使用中の声を切り替える",
        tags=["声"],
        responses=_PROFILE_NOT_FOUND,
    )
    def activate_profile(body: ActiveBody) -> Ok:
        """次の読み上げから効く。"""
        profiles.activate(body.id)
        return Ok()

    @router.patch(
        "/profiles/{profile_id}",
        summary="声の名前と caption を書き換える",
        response_model=ProfileItem,
        tags=["声"],
        responses=_PROFILE_NOT_FOUND,
    )
    def patch_profile(profile_id: str, body: PatchProfileBody):
        """caption は次の読み上げから効き、書くたびに版を残す。"""
        updated = profiles.update(profile_id, name=body.name, caption=body.caption)
        return ProfileItem.model_validate(updated.describe())

    @router.delete(
        "/profiles/{profile_id}",
        summary="声を消す",
        tags=["声"],
        responses={**_PROFILE_NOT_FOUND, 409: _error("`profile_in_use`（使用中の声）")},
    )
    def delete_profile(profile_id: str) -> Ok:
        profiles.delete(profile_id)
        return Ok()

    @router.get(
        "/profiles/{profile_id}/audio",
        summary="声の参照音声",
        tags=["声"],
        response_class=FileResponse,
        responses={**_PROFILE_NOT_FOUND, 200: {"content": {"audio/wav": {}}}},
    )
    def profile_audio(profile_id: str):
        return FileResponse(profiles.require(profile_id).reference, media_type="audio/wav")

    @router.get(
        "/profiles/{profile_id}/caption/history",
        summary="声の caption の履歴",
        tags=["声"],
        responses=_PROFILE_NOT_FOUND,
    )
    def caption_history(profile_id: str) -> HistoryResponse:
        return HistoryResponse.model_validate({"items": profiles.caption_versions(profile_id)})

    @router.post(
        "/profiles/{profile_id}/caption/restore",
        summary="声の caption を履歴の版に戻す",
        tags=["声"],
        responses={**_PROFILE_NOT_FOUND, **_VERSION_ERRORS},
    )
    def restore_caption(profile_id: str, body: RestoreBody) -> Ok:
        profiles.restore_caption(profile_id, body.name)
        return Ok()

    return router
