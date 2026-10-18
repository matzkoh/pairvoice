"""データの置き場所の CRUD（プロンプト・読み辞書・スタイル・声）の HTTP API。

書くのは常駐サーバーだけで、studio の画面はここを呼ぶ。ファイルを読み書きするので、
エンドポイントは def にしてイベントループの外（スレッドプール）で動かす。
"""

from __future__ import annotations

import asyncio
import dataclasses
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Body, Query, Request
from fastapi.openapi.models import Example
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from starlette.datastructures import UploadFile

from . import curate, reading
from .api_errors import BodyTooLarge
from .api_errors import error_doc as _error
from .api_errors import fail as _fail
from .curate import Analysis, InvalidAudio, InvalidSelection, Outcome
from .curate_jobs import CurateJobs
from .profiles import TakeRejected
from .prompt import SummaryPrompt
from .studio_web import studio_url
from .styles import STYLES_FILENAME, StyleStore
from .tts import ANCHOR_TEXT, AudioNotFound, SpeechResult, resolve_data_audio
from .wav import concat_wavs, has_wav_header, mono_wav, write_mono

# 本文で wav を受けるとき（声の取り込みと聞き取り）の上限。48kHz モノラルで9分ほど
UPLOAD_MAX_BYTES = 50 * 1024 * 1024


def declares_too_large(request: Request, limit: int) -> bool:
    declared = request.headers.get("content-length")
    return declared is not None and declared.isdigit() and int(declared) > limit


async def read_wav_body(request: Request) -> bytearray | JSONResponse:
    """本文の wav を読む。大きすぎる・wav でないなら、そのまま返す失敗の応答を返す。"""
    if declares_too_large(request, UPLOAD_MAX_BYTES):
        return _fail(413, "body_too_large")
    audio = bytearray()
    # 申告の無い chunked も読みながら数える
    async for chunk in request.stream():
        audio += chunk
        if len(audio) > UPLOAD_MAX_BYTES:
            return _fail(413, "body_too_large")
    # 中身の妥当性は使う側（mlx-audio）が判断する
    if not has_wav_header(audio):
        return _fail(400, "invalid_audio", "body must be a wav file")
    return audio


# 素材から声を作るときの上限
CURATE_MAX_BYTES = 4 * 1024**3
CURATE_MAX_FILES = 50


def _save_upload(upload: UploadFile, path: Path) -> None:
    with path.open("wb") as sink:
        shutil.copyfileobj(upload.file, sink)


# テイクをつなぐときの数の上限と、間に挟む無音
MAX_TAKES = 8
TAKE_GAP_SECONDS = 0.3
# 伸ばすときの合成の段数（モデル既定は 40）。参照音声は一度しか作らないので丁寧に作る。
# studio の MASTER_STEPS と同じ値にしておく
MASTER_STEPS = 80
# 伸ばすときにテイクの声で読ませる文。Irodori-TTS は同じ話者の短い発話を合わせて 30 秒ほどの
# 参照音声を勧めるので、テイクにこの3つ（各 6〜7 秒）を足してつなぐ。studio の
# REFERENCE_TEXTS（studio/src/features/profiles/mixSynth.ts）と同じ文にしておく
# - 読み上げるのはエージェントの作業の要約なので、落ち着いた説明調を軸にし、問いかけと
#   軽い相づちで抑揚に幅を持たせる。参照音声の話し方は複製した声に移るので、強い感情は入れない
# - 拗音（しゅ・ちょ・じゅ）、促音、撥音、長音、濁音・半濁音、カタカナ語と数を一通り含める
REFERENCE_TEXTS = (
    "ビルドとテストはすべて通りました。変更は三つのファイルにまとまっていて、再起動も済んでいます。",
    "ひとつ確認させてください。この設定は、来週のアップデートまでに切り替えておけば間に合いますか？",
    "ちょっと待ってくださいね。原因はわかったので、じゅうぶん直せそうです。順番に片づけましょう。",
)


class CurateAnalyze(Protocol):
    """Engine.curate_analyze。(ファイル名, パス) の素材を directory にデコードして区間を採点する。"""

    async def __call__(self, files: list[tuple[str, Path]], directory: Path) -> Analysis: ...


class Transcribe(Protocol):
    """Engine.transcribe。wav を聞き取った文。"""

    async def __call__(self, path: Path) -> str: ...


class CurateRender(Protocol):
    """Engine.curate_render。解析した区間から参照音声を作る。selection が None なら自動で選ぶ。"""

    async def __call__(self, analysis: Analysis, selection: list[int] | None) -> Outcome: ...


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
_CURATE_JOB_NOT_FOUND: dict[int | str, dict] = {
    404: _error("`curate_job_not_found`（解析が消えた。素材を送り直す）")
}
# 素材のファイルを multipart で受ける本文
_FILES_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["files"],
                    "properties": {
                        "files": {"type": "array", "items": {"type": "string", "format": "binary"}}
                    },
                }
            }
        },
    }
}
_DESIGN_CAPTION = "落ち着いた低めの男性の声。ゆっくり穏やかに話す。"
_CREATE_EXAMPLES: dict[str, Example] = {
    "takes": {
        "summary": "テイクをつなぐ",
        "value": {
            "name": "落ち着いた声",
            "caption": _DESIGN_CAPTION,
            "takes": ["generations/xxxx.wav", "generations/yyyy.wav"],
        },
    },
    "extend": {
        "summary": "短いテイク1本を伸ばす",
        "value": {
            "name": "落ち着いた声",
            "caption": _DESIGN_CAPTION,
            "takes": ["generations/xxxx.wav"],
            "extend": True,
        },
    },
}
_DESIGN_EXAMPLES: dict[str, Example] = {
    "basic": {
        "summary": "caption だけで作る",
        "value": {"name": "落ち着いた声", "caption": _DESIGN_CAPTION},
    },
    "seed": {
        "summary": "種を固定して作り直せるようにする",
        "value": {"name": "落ち着いた声", "caption": _DESIGN_CAPTION, "rng_seed": 123},
    },
}

# 声の名前と caption は前後の空白を落として保存する。名前は空にさせない
NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ProfileName = NonBlank
Caption = Annotated[str, StringConstraints(strip_whitespace=True)]


class ToneBody(BaseModel):
    text: str = Field(
        description="要約の口調（口調の指示と入出力の例）。口調を持たない声で、共通のプロンプトの後ろに足す"
    )


class ComposedPrompt(BaseModel):
    text: str = Field(description="要約に渡すシステムプロンプト（共通の部分＋口調）")
    voice: str | None = Field(description="口調を足した声の ID。声がまだ無ければ null")


class Ok(BaseModel):
    ok: bool = Field(default=True, description="常に true")


class PromptResponse(BaseModel):
    text: str = Field(description="まだ無ければ空")


class PromptBody(BaseModel):
    text: str = Field(
        description="要約のプロンプトの共通の部分（削り方・言葉選び）。読み上げのたびに声の口調を足して使う"
    )

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


class PreviewRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from", description="置換元。空の行は飛ばす")
    target: str = Field(alias="to", description="置換先（読み）")


class DictTestBody(BaseModel):
    text: str = Field(description="試す文")
    rows: list[PreviewRow] = Field(description="保存していない行でもよい")


class DictTestResponse(BaseModel):
    result: str = Field(description="置き換えた文")


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


Order = Literal["random", "shuffle"]
_ORDER_DESCRIPTION = "選び方。`random` は毎回ばらばらに選ぶ。`shuffle` は混ぜた順に、全員を一巡するまで同じ声を選ばない"


class ProfileItem(BaseModel):
    id: str = Field(description="ID。`voice` に渡せる")
    name: str = Field(description="名前。`voice` に渡せる")
    caption: str = Field(description="話し方の指示。合成のたびに読む")
    source: Literal["design", "upload", "curate", "auto", "import", "random"] = Field(
        description="作り方。`curate` は雑多な音声ファイルから選り分けた。"
        "`random` は参照音声を持たず、要約・合成のたびに `members` から1つを選んで読む"
    )
    created_at: str = Field(description="作った日時（ISO 8601）")
    tone: str = Field(description="要約の口調。空なら既定の口調（`GET /tone`）で読む")
    members: list[str] = Field(description="`random` の声が選ぶ声の ID。ほかの声は空")
    order: Order = Field(description="`random` の声の選び方。ほかの声は `random`")


class CuratedFile(BaseModel):
    name: str = Field(description="ファイル名")
    seconds: float = Field(description="使った長さ（秒）")


class CuratedSpeaker(BaseModel):
    speaker: int = Field(description="話者分離が付けた番号")
    seconds: float = Field(description="その人だけが話している長さの合計（秒）")
    chosen: bool = Field(description="いちばん多く話す人として選んだ")


class CuratedPiece(BaseModel):
    index: int = Field(description="候補の番号（`POST /curate` の `candidates` の `index`）")
    file: str = Field(description="切り出したファイル")
    start: float = Field(description="ファイルの中の始まり（秒）")
    end: float = Field(description="ファイルの中の終わり（秒）")
    snr_db: float = Field(description="見積もった S/N（dB）。隙間の音と、雑音を除いた量の低い方")


class CurateReport(BaseModel):
    files: list[CuratedFile] = Field(description="送った順の素材")
    truncated: bool = Field(description="長すぎるので後ろを使わなかった")
    speakers: list[CuratedSpeaker] = Field(description="話す長さの多い順")
    candidates: int = Field(description="選んだ人だけが話している区間の数")
    seconds: float = Field(description="参照音声の声の長さ（秒）")
    pieces: list[CuratedPiece] = Field(description="参照音声に使った区間（つないだ順）")


class CurateResponse(BaseModel):
    profile: ProfileItem = Field(description="作った声")
    report: CurateReport = Field(description="どの区間を選んだか")


class CurateSource(BaseModel):
    name: str = Field(description="ファイル名")
    offset: float = Field(description="流れの中での始まり（秒）")
    seconds: float = Field(description="長さ（秒）")


class CurateSegment(BaseModel):
    start: float = Field(description="始まり（流れの秒）")
    end: float = Field(description="終わり（流れの秒）")
    speaker: int = Field(description="話者分離が付けた番号")


class CurateCandidate(BaseModel):
    index: int = Field(description="番号。`POST /curate/{job_id}/profile` の `pieces` に渡す")
    file: int = Field(description="`files` の何番目のファイルか")
    start: float = Field(description="始まり（流れの秒）")
    end: float = Field(description="終わり（流れの秒）")
    snr_db: float = Field(description="隙間の音と比べた S/N（dB）")
    loudness_db: float | None = Field(description="声の大きさ（dBFS）")
    pitch_hz: float | None = Field(description="声の高さ（F0 の中央値）。測れなければ null")
    brightness_hz: float | None = Field(description="声の明るさ（スペクトルの重心）")
    deviation: float | None = Field(
        description="ふだんの声からの離れ。大きいほど張り上げた・特徴的な声。"
        "S/N が低すぎて自動では使わない区間は null"
    )


class CurateEnvelope(BaseModel):
    step: float = Field(description="1点の秒数")
    levels: list[int] = Field(description="音の大きさ（0〜100。0 が -60 dBFS 以下、100 が 0 dBFS）")


class CurateLimits(BaseModel):
    min_seconds: float = Field(description="参照音声の声の長さの下限（秒）")
    target_seconds: float = Field(description="自動で選ぶときに集める長さ（秒）")
    max_seconds: float = Field(description="参照音声の声の長さの上限（秒）")


class CurateAnalysisResponse(BaseModel):
    job_id: str = Field(description="解析の ID。区間を選んで声を作るときに使う")
    studio_url: str = Field(
        description="この解析を studio の「素材から作る」で開く URL。利用者に区間を選んでもらうときに渡す"
    )
    files: list[CurateSource] = Field(
        description="送った順の素材。ファイルの間に無音を挟んで1本の流れにつないである"
    )
    truncated: bool = Field(description="長すぎるので後ろを使わなかった")
    speakers: list[CuratedSpeaker] = Field(description="話す長さの多い順")
    segments: list[CurateSegment] = Field(description="話者分離の区間（全員）")
    candidates: list[CurateCandidate] = Field(
        description="いちばん多く話す人だけが話している区間（流れの順）"
    )
    preselected: list[int] = Field(
        description="自動で選ぶ見込みの候補の番号。雑音を除いた結果で入れ替わることがある"
    )
    envelope: CurateEnvelope = Field(description="素材全体の音の大きさ")
    limits: CurateLimits = Field(description="参照音声の声の長さの決まり")


class CurateTranscript(BaseModel):
    text: str = Field(description="聞き取った文")


class CurateProfileBody(BaseModel):
    name: ProfileName = Field(description="声の名前")
    caption: Caption = Field(default="", description="話し方の指示")
    pieces: list[int] | None = Field(
        default=None,
        description="使う候補の番号。省くか null なら自動で選ぶ（`POST /profiles/curate` と同じ）",
    )


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


class RandomProfileBody(BaseModel):
    name: ProfileName = Field(description="声の名前")
    members: list[str] = Field(
        description="選ぶ声の ID（2つ以上）。`random` の声は入れられない。"
        "選んだ声の caption と口調で読む",
    )
    order: Order = Field(default="random", description=_ORDER_DESCRIPTION)


class ActiveBody(BaseModel):
    id: str = Field(description="使用中にする声の ID")


class PatchProfileBody(BaseModel):
    name: ProfileName | None = Field(default=None, description="声の名前")
    caption: Caption | None = Field(
        default=None, description="空文字は caption なしで読む。書くたびに版を残す"
    )
    tone: str | None = Field(
        default=None,
        description="要約の口調（口調の指示と入出力の例）。共通のプロンプトの後ろに足す。"
        "空文字は既定の口調で読む。書くたびに版を残す",
    )
    members: list[str] | None = Field(
        default=None, description="`random` の声が選ぶ声の ID（2つ以上）"
    )
    order: Order | None = Field(default=None, description=f"`random` の声の{_ORDER_DESCRIPTION}")

    @model_validator(mode="after")
    def _something_to_change(self):
        if all(v is None for v in (self.name, self.caption, self.tone, self.members, self.order)):
            raise ValueError("name, caption, tone, members or order is required")
        return self


def data_router(
    data_root: Path,
    synthesize: Synthesize,
    curate_analyze: CurateAnalyze,
    curate_render: CurateRender,
    transcribe: Transcribe,
) -> APIRouter:
    router = APIRouter()
    curate_jobs = CurateJobs()
    dict_path = data_root / reading.DICT_FILENAME
    styles = StyleStore(data_root / STYLES_FILENAME)
    summary_prompt = SummaryPrompt(data_root)
    profiles = summary_prompt.profiles
    prompt = summary_prompt.common

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

    @router.get(
        "/prompt/composed",
        summary="声ごとに組んだ要約のプロンプト",
        tags=["プロンプト"],
        responses=_PROFILE_NOT_FOUND,
    )
    def composed_prompt(
        voice: Annotated[
            str | None, Query(description="声の名前か ID。省くと使用中のプロファイル")
        ] = None,
    ) -> ComposedPrompt:
        """読み上げの要約に渡すものと同じ、共通の部分にその声の口調を足したもの。"""
        text, profile = summary_prompt.compose_for(voice)
        return ComposedPrompt(text=text, voice=None if profile is None else profile.id)

    # ---- 口調（既定と、声ごと） ----

    tone = summary_prompt.default_tone

    @router.get("/tone", summary="既定の口調", tags=["プロンプト"])
    def get_tone() -> PromptResponse:
        """口調を持たない声が使う。声の口調はプロファイルの `tone`。"""
        return PromptResponse(text=tone.read())

    @router.put("/tone", summary="既定の口調を書き換える", tags=["プロンプト"])
    def put_tone(body: ToneBody) -> Ok:
        """書いた内容を履歴の最新版として残す。次の読み上げから効く。"""
        tone.write(body.text)
        return Ok()

    @router.get("/tone/history", summary="既定の口調の履歴", tags=["プロンプト"])
    def tone_history() -> HistoryResponse:
        return HistoryResponse.model_validate({"items": tone.versions()})

    @router.post(
        "/tone/restore",
        summary="既定の口調を履歴の版に戻す",
        tags=["プロンプト"],
        responses=_VERSION_ERRORS,
    )
    def restore_tone(body: RestoreBody) -> Ok:
        tone.restore(body.name)
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

    @router.post("/dict/test", summary="読み辞書を保存せずに試す", tags=["読み辞書"])
    def test_dict(body: DictTestBody) -> DictTestResponse:
        """合成の直前と同じ置き換えを、渡した行でかける。"""
        rows = [(row.source, row.target) for row in body.rows if row.source]
        return DictTestResponse(result=reading.apply_dict(body.text, rows))

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
    async def create_profile(
        body: Annotated[CreateProfileBody, Body(openapi_examples=_CREATE_EXAMPLES)],
    ):
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
    async def design_profile(
        body: Annotated[DesignProfileBody, Body(openapi_examples=_DESIGN_EXAMPLES)],
    ):
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
        audio = await read_wav_body(request)
        if isinstance(audio, JSONResponse):
            return audio
        # 数十 MB を書くので、イベントループの外で行う
        created = await asyncio.to_thread(
            profiles.create,
            name=name,
            caption=caption,
            source="upload",
            write_reference=lambda path: path.write_bytes(audio),
        )
        return ProfileItem.model_validate(created.describe())

    @router.post(
        "/profiles/curate",
        status_code=201,
        response_model=CurateResponse,
        summary="雑多な音声ファイルから声を作る",
        tags=["声"],
        openapi_extra=_FILES_BODY,
        responses={
            400: _error("`invalid_audio`（ファイルが無い・読めない）"),
            413: _error(f"`body_too_large`（{CURATE_MAX_BYTES // 1024**3}GB まで）"),
            422: _error("`too_little_speech`（きれいな声が足りない）"),
            503: _error("`ffmpeg_missing`、またはモデルを使えない"),
        },
    )
    async def curate_profile(
        request: Request,
        name: Annotated[ProfileName, Query(description="声の名前")],
        caption: Annotated[Caption, Query(description="話し方の指示")] = "",
    ):
        """`files` に音声・動画のファイルをいくつでも送る（形式は ffmpeg が読めるもの）。

        いちばん多く話す人の声から、BGM や雑音が少なく、その人のふだんの話し方に近い区間を
        合わせて 26〜28 秒ほど選び、雑音を除いてつなぐ。ファイルをまたいでも同じ人は同じ人と
        みなす（全体で4人まで）。1時間の素材で数分かかり、その間も読み上げは合間に割り込む。
        使用中の声がまだ無ければ、作った声を使用中にする。区間を自分で選ぶなら `POST /curate`。
        """
        with tempfile.TemporaryDirectory() as directory:
            files = await _receive_files(request, Path(directory))
            analysis = await curate_analyze(files, Path(directory))
            outcome = await curate_render(analysis, None)
        return await _create_curated(name, caption, outcome)

    async def _create_curated(name: str, caption: str, outcome: Outcome) -> dict:
        created = await asyncio.to_thread(
            profiles.create,
            name=name,
            caption=caption,
            source="curate",
            write_reference=lambda path: write_mono(path, outcome.samples, outcome.sample_rate),
        )
        return {"profile": created.describe(), "report": outcome.report}

    @router.post(
        "/curate",
        status_code=201,
        response_model=CurateAnalysisResponse,
        summary="素材を解析して区間の候補を出す",
        tags=["声"],
        openapi_extra=_FILES_BODY,
        responses={
            400: _error("`invalid_audio`（ファイルが無い・読めない）"),
            413: _error(f"`body_too_large`（{CURATE_MAX_BYTES // 1024**3}GB まで）"),
            422: _error("`too_little_speech`（声が無い）"),
            503: _error("`ffmpeg_missing`、またはモデルを使えない"),
        },
    )
    async def analyze_curate(request: Request):
        """`POST /profiles/curate` を2段に分けた前半。区間を自分で選ぶときに使う。

        `files` の素材から、いちばん多く話す人だけが話している区間を候補として返す。候補を
        `GET /curate/{job_id}/audio` で聴き、`POST /curate/{job_id}/profile` に番号を渡して声を作る。
        区間を利用者に選んでもらうなら、`studio_url` を渡す（studio で聴いて選び、そのまま作れる）。
        解析は最後に使ってから1時間か、新しい解析が3つを超えると消える。
        """
        directory = tempfile.TemporaryDirectory(prefix="pairvoice-curate-")
        try:
            files = await _receive_files(request, Path(directory.name))
            analysis = await curate_analyze(files, Path(directory.name))
        except BaseException:
            directory.cleanup()
            raise
        job_id = curate_jobs.add(analysis, directory)
        return await _describe_job(request, job_id, analysis)

    @router.get(
        "/curate/{job_id}",
        response_model=CurateAnalysisResponse,
        summary="解析した候補",
        tags=["声"],
        responses=_CURATE_JOB_NOT_FOUND,
    )
    async def get_curate(request: Request, job_id: str):
        """`POST /curate` と同じ形で返す。studio が URL から解析を開くのに使う。"""
        return await _describe_job(request, job_id, curate_jobs.get(job_id))

    async def _describe_job(request: Request, job_id: str, analysis: Analysis) -> dict:
        described = await asyncio.to_thread(curate.describe, analysis)
        # 呼び手が届いた Host のまま返す（設定の URL と違う名前で来ていることがある）
        url = studio_url(str(request.base_url), "profiles", id="new", curate=job_id)
        return {"job_id": job_id, "studio_url": url, **described}

    @router.get(
        "/curate/{job_id}/audio",
        response_class=Response,
        summary="素材の一部を聴く",
        tags=["声"],
        responses={
            200: {"content": {"audio/wav": {}}, "description": "48kHz モノラルの wav"},
            **_CURATE_JOB_NOT_FOUND,
        },
    )
    def curate_audio(
        job_id: str,
        start: Annotated[float, Query(ge=0, description="始まり（流れの秒）")],
        end: Annotated[float, Query(gt=0, description="終わり（流れの秒）")],
    ):
        """雑音を除く前の元の音（声の大きいチャンネル）。一度に60秒まで。"""
        analysis = curate_jobs.get(job_id)
        samples = curate.excerpt(analysis.material, start, end)
        return Response(mono_wav(samples, curate.OUTPUT_RATE), media_type="audio/wav")

    @router.post(
        "/curate/{job_id}/candidates/{index}/transcript",
        response_model=CurateTranscript,
        summary="候補のセリフを聞き取る",
        tags=["声"],
        responses={
            **_CURATE_JOB_NOT_FOUND,
            400: _error("`invalid_selection`（候補の番号が無い）"),
            503: _error("聞き取りのモデルを使えない"),
        },
    )
    async def transcribe_candidate(job_id: str, index: int):
        """候補の区間を聞き取る。一度聞いた候補は覚えておいて、すぐ返す。

        聞き取りは合成と同じ列に並ぶ。たくさんの候補をまとめて頼まず、1つずつ順に頼む
        （まとめると列を占めて、読み上げが待たされる）。
        """
        analysis = curate_jobs.get(job_id)
        if not 0 <= index < len(analysis.pieces):
            raise InvalidSelection(f"候補の番号は 0〜{len(analysis.pieces) - 1} です")
        if index not in analysis.transcripts:
            # 頼んだ側が待つのをやめても（画面を離れても）、聞き取りは終えて覚えておく
            analysis.transcripts[index] = asyncio.ensure_future(
                _transcribe_piece(analysis.material, analysis.pieces[index])
            )
        try:
            text = await asyncio.shield(analysis.transcripts[index])
        except Exception:
            # 失敗は覚えない（次に頼まれたら聞き直す）
            analysis.transcripts.pop(index, None)
            raise
        return {"text": text}

    async def _transcribe_piece(material: curate.Material, piece: curate.Piece) -> str:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.wav"

            def write() -> None:
                samples = curate.excerpt(material, piece.start, piece.end)
                write_mono(path, samples, curate.OUTPUT_RATE)

            await asyncio.to_thread(write)
            return await transcribe(path)

    @router.post(
        "/curate/{job_id}/profile",
        status_code=201,
        response_model=CurateResponse,
        summary="選んだ区間から声を作る",
        tags=["声"],
        responses={
            **_CURATE_JOB_NOT_FOUND,
            400: _error("`invalid_selection`（番号が無い・重なる・長さが足りない・長すぎる）"),
            422: _error("`too_little_speech`（自動で選んだとき、きれいな声が足りない）"),
            503: _error("モデルを使えない"),
        },
    )
    async def create_curated_profile(job_id: str, body: CurateProfileBody):
        """選んだ区間の雑音を除いてつなぐ。解析は残るので、選び直して何度でも作れる。"""
        analysis = curate_jobs.get(job_id)
        outcome = await curate_render(analysis, body.pieces)
        return await _create_curated(body.name, body.caption, outcome)

    @router.delete(
        "/curate/{job_id}",
        summary="解析を消す",
        tags=["声"],
        responses=_CURATE_JOB_NOT_FOUND,
    )
    def delete_curate(job_id: str) -> Ok:
        """デコードした素材を消す。消さなくても1時間で消える。"""
        curate_jobs.remove(job_id)
        return Ok()

    async def _receive_files(request: Request, directory: Path) -> list[tuple[str, Path]]:
        """multipart の `files` を directory に書き、(送られた名前, パス) を返す。"""
        if declares_too_large(request, CURATE_MAX_BYTES):
            raise BodyTooLarge
        async with request.form(max_files=CURATE_MAX_FILES) as form:
            uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
            if not uploads:
                raise InvalidAudio("files is empty")
            files = []
            for index, upload in enumerate(uploads):
                # 送られた名前はパスに使わない（../ で外へ出られる）
                path = directory / f"upload-{index}"
                await asyncio.to_thread(_save_upload, upload, path)
                files.append((upload.filename or f"{index + 1} 本目", path))
        return files

    @router.post(
        "/profiles/random",
        status_code=201,
        response_model=ProfileItem,
        summary="ほかの声から毎回1つを選ぶ声を作る",
        tags=["声"],
        responses={
            **_PROFILE_NOT_FOUND,
            400: _error("`invalid_profile`（メンバーが2つに満たない・`random` の声を含む）"),
        },
    )
    def create_random_profile(body: RandomProfileBody):
        """使用中にしたり `voice` に渡したりすると、要約・合成のたびにメンバーから1つを選んで読む。"""
        created = profiles.create_random(name=body.name, members=body.members, order=body.order)
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
        summary="声の名前・caption・口調・メンバー・選び方を書き換える",
        response_model=ProfileItem,
        tags=["声"],
        responses={
            **_PROFILE_NOT_FOUND,
            400: _error(
                "`invalid_profile`（`random` の声の caption・口調、ほかの声のメンバー・選び方、"
                "メンバーの不備）"
            ),
        },
    )
    def patch_profile(profile_id: str, body: PatchProfileBody):
        """caption と口調は次の読み上げから効き、書くたびに版を残す。"""
        updated = profiles.update(
            profile_id,
            name=body.name,
            caption=body.caption,
            tone=body.tone,
            members=body.members,
            order=body.order,
        )
        return ProfileItem.model_validate(updated.describe())

    @router.delete(
        "/profiles/{profile_id}",
        summary="声を消す",
        tags=["声"],
        responses={
            **_PROFILE_NOT_FOUND,
            409: _error("`profile_in_use`（使用中の声・`random` の声のメンバー）"),
        },
    )
    def delete_profile(profile_id: str) -> Ok:
        profiles.delete(profile_id)
        return Ok()

    @router.get(
        "/profiles/{profile_id}/audio",
        summary="声の参照音声",
        tags=["声"],
        response_class=FileResponse,
        responses={
            **_PROFILE_NOT_FOUND,
            200: {"content": {"audio/wav": {}}},
            404: _error("`profile_not_found`、`audio_not_found`（`random` の声）"),
        },
    )
    def profile_audio(profile_id: str):
        reference = profiles.require(profile_id).reference
        if reference is None:
            raise AudioNotFound(profile_id)
        return FileResponse(reference, media_type="audio/wav")

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

    @router.get(
        "/profiles/{profile_id}/tone/history",
        summary="声の口調の履歴",
        tags=["声"],
        responses=_PROFILE_NOT_FOUND,
    )
    def tone_history_of(profile_id: str) -> HistoryResponse:
        return HistoryResponse.model_validate({"items": profiles.tone_versions(profile_id)})

    @router.post(
        "/profiles/{profile_id}/tone/restore",
        summary="声の口調を履歴の版に戻す",
        tags=["声"],
        responses={**_PROFILE_NOT_FOUND, **_VERSION_ERRORS},
    )
    def restore_tone_of(profile_id: str, body: RestoreBody) -> Ok:
        profiles.restore_tone(profile_id, body.name)
        return Ok()

    return router
