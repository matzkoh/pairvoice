"""FastAPI のルーティング。判断は Engine に置き、ここは HTTP との変換だけを担う。"""

from __future__ import annotations

import asyncio
import os
import signal
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, Body, FastAPI, Request
from fastapi.openapi.models import Example
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from . import evaluation, launchd
from .api_errors import ErrorResponse, fail, install_error_handlers
from .api_errors import error_doc as _error
from .audio_state import AudioProbe
from .config import Config
from .corpus_api import corpus_router
from .data_api import data_router
from .lifecycle import Engine, ModelUnavailable, MutedError, Superseded, limit_mlx_cache
from .llm import MlxLmBackend
from .mute import MAX_MINUTES, MIN_MINUTES, InvalidMinutes, MuteController
from .prompt import PromptStore
from .studio_web import mount_studio, open_studio
from .tts import MlxAudioBackend

# ポートは問わない（開発中の Vite の :17493 から来るため）
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _hostname(netloc: str) -> str:
    # 末尾の :port を落とす。IPv6 は [::1]:port なので、角括弧の外のコロンだけを見る
    host, _, port = netloc.rpartition(":")
    return (host if port.isdigit() and not netloc.endswith("]") else netloc).lower()


def is_local_request(host: str | None, origin: str | None) -> bool:
    """ブラウザで開いた外部のページからの要求を断るための判定。

    127.0.0.1 にだけ待ち受けても、外部のページから2つの経路で届く。DNS rebinding では Host が
    外部の名前のまま届き、単純な POST（no-cors）では Host は 127.0.0.1 でも Origin に外部の
    ページが付く。Origin はブラウザしか付けないので、無ければ通す（フック、CLI）。
    """
    if host is None or _hostname(host) not in LOCAL_HOSTNAMES:
        return False
    return origin is None or _hostname(urlsplit(origin).netloc) in LOCAL_HOSTNAMES


MAX_TOKENS_LIMIT = 1024

API_DESCRIPTION = """\
pairvoice の常駐サーバーの API。要約（mlx-lm）と音声合成（Irodori-TTS）を1本のキューで順に処理する。

- 待ち受けは 127.0.0.1 だけ。ブラウザで開いた外部のページ（Host か Origin がループバックでない要求）は 403 で断る
- ミュート中は `/speak`（鳴らす）と `respect_mute` 付きの `/llm` を 409 `{"error": "muted", "reason": ...}` で断る。`/speak` は `bypass_mute` で鳴らせる。`/synthesize` は wav を作るだけなのでミュートを見ない
- 声は `voice`（プロファイルの名前か ID）、話し方は `style`（studio の「スタイル」画面で作る名前）で選ぶ。選べる名前は `GET /profiles` と `GET /styles` で引ける
"""

# /docs の「Try it out」の初期値。例が無いと Swagger UI はすべての項目を 0 で埋めた本文を出し、
# そのまま送ると num_steps: 0 などの壊れた合成になる
_SPEAK_BASIC = {"text": "テスト全部通ったよ。"}
_SPEAK_EXAMPLES: dict[str, Example] = {
    "basic": {"summary": "使用中の声で読む", "value": _SPEAK_BASIC},
    "voice_and_style": {
        "summary": "声とスタイルを選ぶ",
        "value": {**_SPEAK_BASIC, "voice": "優しい", "style": "ゆっくり"},
    },
}
_SUMMARY_EXAMPLES: dict[str, Example] = {
    "basic": {
        "summary": "要約する",
        "value": {"system": "日本語で一文に要約する。", "prompt": "テストがすべて通った。"},
    }
}

# 要約と合成の両方にある項目。説明を1か所に置く
WaitDownload = Annotated[
    bool,
    Field(
        description="false なら、初回のモデルのダウンロード中は待たずに 503 `model_downloading` を返す"
    ),
]
DataAudioPath = Annotated[str, Field(description="データの置き場所からの相対パス（wav）")]


_MODEL_UNAVAILABLE = _error(
    "モデルを使えない。`error` は `model_downloading`（初回のダウンロード中で、"
    "`wait_download: false` のとき）、`model_load_failed`（読み込みの失敗・時間切れ）、"
    "`profile_missing`（設定の参照音声が無い）のどれか"
)
_STYLE_INVALID = _error("`style_invalid`（styles.json が壊れている）")


class MutedResponse(BaseModel):
    error: Literal["muted"] = Field(description="ミュート中で、合成・要約しなかった")
    reason: str = Field(
        description="`manual`（手動）、`microphone`（マイク使用中）、`audio_output`（ほかのアプリが音を出している）など"
    )


class SummaryResponse(BaseModel):
    text: str = Field(description="要約した文")


class SynthesisResponse(BaseModel):
    path: str = Field(description="合成した wav の絶対パス")
    relative_path: str = Field(description="データの置き場所からの相対パス")
    duration: float = Field(description="長さ（秒）")


class SummaryRequest(BaseModel):
    system: str = Field(description="システムプロンプト（要約の指示）")
    prompt: str = Field(description="要約する文")
    # mlx_lm は負の値を無制限とみなす。上限は読み上げ要約の既定（128）に十分な余裕を
    # 持たせた値で、これを超える生成は Runner を塞いでフックの読み上げを待たせるだけ
    max_tokens: StrictInt | None = Field(
        default=None,
        ge=1,
        le=MAX_TOKENS_LIMIT,
        description="生成する最大トークン数。省くと config.toml の `[llm] max_tokens`",
    )
    respect_mute: bool = Field(
        default=False,
        description="ミュート中なら要約せず 409 `muted` を返す（読み上げに使う要約のため）",
    )
    droppable: bool = Field(
        default=True,
        description="待っている間に後発の要約が来たら、これを捨てて 409 `dropped` を返す（読み上げの古い要約を飛ばすため）",
    )
    wait_download: WaitDownload = True


class SamplerOverrides(BaseModel):
    """Irodori-TTS のサンプラーの上書き。省いた項目はスタイル → config.toml → モデル既定の順で決まる。

    フィールド名は mlx-audio の generate() の引数名そのまま。名前がずれた値は
    黙って捨てられるので、途中で変換しない。extra="forbid" は、キーの打ち間違いを
    422 にして「指定したのに効かない」を起こさないため。

    未指定（None）は「config.toml とモデル既定に任せる」の意味で、そのまま送ると
    config の値を潰してしまうので model_dump(exclude_none=True) で落とす。
    """

    model_config = ConfigDict(extra="forbid")

    # 説明に既定値を書かない。正本はモデルの config.json と config.toml（SamplerConfig と同じ方針）
    num_steps: int | None = Field(default=None, description="サンプリングのステップ数")
    cfg_guidance_mode: str | None = Field(
        default=None, description="`independent`、`joint`、`alternating` のどれか"
    )
    cfg_scale_text: float | None = Field(default=None, description="読む文への忠実さ")
    cfg_scale_caption: float | None = Field(default=None, description="caption の効く強さ")
    cfg_scale_speaker: float | None = Field(default=None, description="参照音声の声への寄せ方")
    t_schedule_mode: str | None = Field(default=None, description="`linear` か `sway`")
    sway_coeff: float | None = Field(default=None, description="`sway` のときの係数")
    duration_scale: float | None = Field(
        default=None, description="予測した長さに掛ける倍率。1 より大きいとゆっくり"
    )
    seconds: float | None = Field(default=None, description="長さを秒で固定する。省くと自動")
    rng_seed: int | None = Field(default=None, description="乱数の種。同じ条件と種なら同じ音になる")
    cfg_min_t: float | None = Field(default=None, description="CFG を掛ける timestep の下限")
    cfg_max_t: float | None = Field(default=None, description="CFG を掛ける timestep の上限")
    context_kv_cache: bool | None = Field(
        default=None, description="条件の K/V を使い回して速くする（音は変わらない）"
    )
    speaker_kv_scale: float | None = Field(
        default=None, description="話者性を強める追加の倍率（実験的）"
    )
    truncation_factor: float | None = Field(
        default=None, description="初期ノイズに掛ける倍率。小さいとばらつきが減る"
    )
    rescale_k: float | None = Field(default=None, description="スコア再スケールの係数")
    rescale_sigma: float | None = Field(default=None, description="スコア再スケールの幅")


class MixPart(BaseModel):
    audio: DataAudioPath
    # 伸ばしすぎると声が崩れるので、2択が使う幅（負の重みの合計 0.5 まで）に余裕を
    # 持たせた範囲に絞る
    weight: float = Field(
        ge=-1, le=2, description="重み。負の値はその声から遠ざかる向きへ伸ばす（外挿）"
    )


class SynthesisRequest(BaseModel):
    """/speak と /synthesize の両方にある項目。"""

    text: str = Field(description="読む文。合成の直前に読み辞書（dict.tsv）で読みに開く")
    voice: str | None = Field(
        default=None,
        description="声（プロファイル）の名前か ID。同じ名前が複数あれば、いちばん新しく作ったもの。"
        "省くと使用中のプロファイル",
    )
    style: str | None = Field(
        default=None,
        description="スタイルの名前。caption と sampler の既定をそのスタイルのものに替える。"
        "変えられるのは速さ・テンション・抑揚までで、声質は `voice` で選ぶ",
    )
    caption: str | None = Field(
        default=None,
        description="話し方の指示。省くとスタイル → プロファイルの caption の順で決まる。空文字は caption なしで読む",
    )
    wait_download: WaitDownload = True


class SpeakRequest(SynthesisRequest):
    bypass_mute: bool = Field(default=False, description="ミュート中でも鳴らす")


class SynthesizeRequest(SynthesisRequest):
    sampler: SamplerOverrides | None = Field(
        default=None, description="項目ごとにスタイルと config.toml より優先する"
    )
    design: bool = Field(
        default=False,
        description="参照音声を使わず caption だけで声を作る（studio の候補づくり用）。"
        "作るたびに違う声になるので、同じ声で読ませ続けるなら `POST /profiles/design` でプロファイルにする",
    )
    mix: list[MixPart] | None = Field(
        default=None,
        min_length=1,
        description="studio の「2択で絞り込む」用。いくつかの声の話者の表現を重みで混ぜた声で読む",
    )

    @model_validator(mode="after")
    def _mix_weights_sum_positive(self):
        # 重みは足して1に直して混ぜるので、和が 0 以下では声にならない
        if self.mix is not None and sum(part.weight for part in self.mix) <= 0:
            raise ValueError("mix weights must sum to a positive value")
        return self


class SpeakerVectorRequest(BaseModel):
    audio: DataAudioPath


class EvalCase(BaseModel):
    id: str = Field(description="結果に添える ID")
    input: str = Field(description="要約する作業ログ")


class EvalRequest(BaseModel):
    prompt: str | None = Field(
        default=None, description="評価するシステムプロンプト。省くと使用中の prompt.txt"
    )
    cases: list[EvalCase] | None = Field(default=None, description="省くと同梱のケース")
    reviews: bool = Field(default=False, description="studio のレビュー（👍 / 👎）もケースに加える")


class MuteRequest(BaseModel):
    # 既定の lax だと true が 1 に化ける。範囲の判定は MuteController に任せる（400）
    minutes: StrictInt | None = Field(
        default=None,
        description=f"ミュートする分数（{MIN_MINUTES}〜{MAX_MINUTES}）。省くと解除するまで",
    )


def build_engine(config: Config) -> Engine:
    limit_mlx_cache()
    probe = AudioProbe(ignore_processes=config.mute.audio_output_ignore_processes)
    return Engine(
        config=config,
        llm_backend=MlxLmBackend(config.llm),
        tts_backend=MlxAudioBackend(config.tts),
        mute=MuteController(config.mute, probe),
    )


def create_app(engine: Engine) -> FastAPI:
    data_root = engine.config.tts.data_root

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="pairvoice",
        version=version("pairvoice"),
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )

    install_error_handlers(app)

    # 応答の形が {"error", "detail"} でない2つ
    @app.exception_handler(MutedError)
    async def muted(request: Request, error: MutedError):
        return JSONResponse(status_code=409, content={"error": "muted", "reason": error.reason})

    @app.exception_handler(ModelUnavailable)
    async def model_unavailable(request: Request, error: ModelUnavailable):
        return fail(503, error.code, error.detail)

    @app.middleware("http")
    async def reject_foreign_pages(request: Request, call_next):
        if not is_local_request(request.headers.get("host"), request.headers.get("origin")):
            return JSONResponse(status_code=403, content={"error": "forbidden_origin"})
        return await call_next(request)

    @app.post(
        "/llm",
        summary="要約する",
        tags=["読み上げ"],
        response_model=SummaryResponse,
        responses={
            409: {
                "model": MutedResponse | ErrorResponse,
                "description": "ミュート中（`respect_mute` のとき）か、"
                "後発の要約に追い越されて捨てた（`droppable` のとき、`error` は `dropped`）",
            },
            503: _MODEL_UNAVAILABLE,
        },
    )
    async def summarize(
        request: Annotated[SummaryRequest, Body(openapi_examples=_SUMMARY_EXAMPLES)],
    ):
        try:
            text = await engine.summarize(
                system=request.system,
                prompt=request.prompt,
                max_tokens=request.max_tokens,
                respect_mute=request.respect_mute,
                droppable=request.droppable,
                wait_download=request.wait_download,
            )
        except Superseded:
            return JSONResponse(status_code=409, content={"error": "dropped"})
        return {"text": text}

    _VOICE_NOT_FOUND = (
        "`profile_not_found`（`voice` の声が無い）、`style_not_found`（`style` が無い）"
    )
    _SYNTHESIS_ERRORS: dict[int | str, dict] = {
        404: _error(_VOICE_NOT_FOUND),
        500: _STYLE_INVALID,
        503: _MODEL_UNAVAILABLE,
    }

    def speech_response(result) -> dict:
        return {
            "path": str(result.path),
            "relative_path": result.relative_path,
            "duration": result.duration,
        }

    @app.post(
        "/speak",
        summary="合成して鳴らす",
        tags=["読み上げ"],
        response_model=SynthesisResponse,
        responses={409: {"model": MutedResponse, "description": "ミュート中"}, **_SYNTHESIS_ERRORS},
    )
    async def speak(request: Annotated[SpeakRequest, Body(openapi_examples=_SPEAK_EXAMPLES)]):
        """再生の列に積んだら返り、鳴り終わるのは待たない。積んだあとに入ったミュートでは鳴らない。"""
        result = await engine.speak(
            request.text,
            bypass_mute=request.bypass_mute,
            caption=request.caption,
            profile_id=request.voice,
            wait_download=request.wait_download,
            style=request.style,
        )
        return speech_response(result)

    @app.post(
        "/synthesize",
        summary="合成する（鳴らさない）",
        tags=["読み上げ"],
        response_model=SynthesisResponse,
        responses={
            **_SYNTHESIS_ERRORS,
            404: _error(f"{_VOICE_NOT_FOUND}、`audio_not_found`（`mix` の wav が無い）"),
        },
    )
    async def synthesize(
        request: Annotated[SynthesizeRequest, Body(openapi_examples=_SPEAK_EXAMPLES)],
    ):
        """wav を作るだけで、ミュートは見ない。studio の試聴と声づくりが使う。"""
        result = await engine.synthesize(
            request.text,
            caption=request.caption,
            sampler=(
                request.sampler.model_dump(exclude_none=True)
                if request.sampler is not None
                else None
            ),
            design=request.design,
            profile_id=request.voice,
            wait_download=request.wait_download,
            mix=(
                [(part.audio, part.weight) for part in request.mix]
                if request.mix is not None
                else None
            ),
            style=request.style,
        )
        return speech_response(result)

    @app.post(
        "/speaker-vector",
        summary="話者ベクトルを測る（studio 用）",
        tags=["声"],
        responses={404: _error("`audio_not_found`"), 503: _MODEL_UNAVAILABLE},
    )
    async def speaker_vector(request: SpeakerVectorRequest):
        return {"vector": await engine.speaker_vector(request.audio)}

    @app.get("/health", summary="状態", tags=["状態"])
    async def health():
        """モデルの状態、使用中の声、ミュート、キューの混み具合。"""
        return engine.health()

    @app.post("/warmup", status_code=202, summary="モデルを読み込む", tags=["状態"])
    async def warmup():
        """要約と合成のモデルを読み込み、終わるまで待つ（初回はダウンロードも）。"""
        return await engine.warmup()

    @app.get("/mute", summary="ミュートの状態", tags=["ミュート"])
    async def mute_state():
        return engine.mute.state().describe()

    @app.post("/stop", summary="読み上げを止める", tags=["読み上げ"])
    async def stop():
        """鳴っている読み上げと、順番待ちの読み上げをすべて捨てる。"""
        return {"stopped": engine.player.stop()}

    @app.post(
        "/mute",
        summary="ミュートする",
        tags=["ミュート"],
        responses={400: _error("`invalid_minutes`（範囲の外）")},
    )
    async def mute(request: MuteRequest):
        try:
            state = engine.mute.mute(request.minutes)
        except InvalidMinutes as error:
            return fail(400, "invalid_minutes", str(error))
        return state.describe()

    @app.post("/unmute", summary="ミュートを解く", tags=["ミュート"])
    async def unmute():
        state = engine.mute.unmute()
        return {"active": state.active}

    @app.post("/restart", status_code=202, summary="常駐サーバーを再起動する", tags=["運用"])
    async def restart(background: BackgroundTasks):
        """応答を返してから LaunchAgent に再起動させる。config.toml の変更を反映するのに使う。"""
        background.add_task(launchd.restart)
        return {"restarting": True}

    @app.post("/shutdown", status_code=202, summary="常駐サーバーを終了する", tags=["運用"])
    async def shutdown(background: BackgroundTasks):
        """応答を返してから終了する。メニューの「pairvoice を終了」と同じく、LaunchAgent は復活させない。"""
        # serve の signal ハンドラがメニューバーを片付けて終了コード 0 で降りる
        background.add_task(os.kill, os.getpid(), signal.SIGTERM)
        return {"stopping": True}

    @app.post("/studio/open", summary="studio をブラウザで開く", tags=["運用"])
    def open_studio_page():
        """studio の画面はこの常駐サーバーが `/studio/` で配っている。"""
        return {"url": open_studio(f"http://127.0.0.1:{engine.config.port}")}

    def load_eval_inputs(request: EvalRequest) -> tuple[str, list[evaluation.Case]]:
        system = request.prompt if request.prompt is not None else PromptStore(data_root).read()
        if request.cases is not None:
            cases = [evaluation.Case(id=c.id, input=c.input) for c in request.cases]
        else:
            cases = evaluation.load_tsv_cases(evaluation.DEFAULT_CASES)
        if request.reviews:
            cases += evaluation.load_review_cases(data_root)
        return system, cases

    @app.post(
        "/eval",
        summary="要約プロンプトを評価する",
        tags=["運用"],
        responses={503: _MODEL_UNAVAILABLE},
    )
    async def evaluate(request: EvalRequest):
        """ケースを常駐サーバーのモデルで要約し、規則で判定する（`pairvoice eval` と同じ）。

        ケースの数だけ要約するので時間がかかる。読み上げの要約とは同じ列に並ぶ。
        """
        # corpus.jsonl は伸び続けるので、ファイルを読むのはイベントループの外で行う
        system, cases = await asyncio.to_thread(load_eval_inputs, request)
        results = []
        for case in cases:
            output = await engine.summarize(system, case.input, droppable=False)
            results.append(evaluation.judge(case, output, engine.config.eval.style))
        return {"summary": evaluation.format_summary(results), "results": results}

    app.include_router(data_router(data_root, engine.synthesize))
    app.include_router(corpus_router(data_root))
    # 画面の道筋は API のあとに足す（API と同じ名前を画面に取られない）
    mount_studio(app)
    return app
