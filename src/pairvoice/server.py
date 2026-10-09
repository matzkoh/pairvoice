"""FastAPI のルーティング。判断は Engine に置き、ここは HTTP との変換だけを担う。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .audio_state import AudioProbe
from .config import Config
from .lifecycle import Engine, ModelUnavailable, MutedError, Superseded, limit_mlx_cache
from .llm import MlxLmBackend
from .mute import InvalidMinutes, MuteController
from .profiles import ProfileNotFound
from .tts import AudioNotFound, MlxAudioBackend

# studio（studio/server/app.ts）と同じ基準。ポートは問わない
LOCAL_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _hostname(netloc: str) -> str:
    # 末尾の :port を落とす。IPv6 は [::1]:port なので、角括弧の外のコロンだけを見る
    host, _, port = netloc.rpartition(":")
    return (host if port.isdigit() and not netloc.endswith("]") else netloc).lower()


def is_local_request(host: str | None, origin: str | None) -> bool:
    """ブラウザで開いた外部のページからの要求を断るための判定。

    127.0.0.1 にだけ待ち受けても、外部のページから2つの経路で届く。DNS rebinding では Host が
    外部の名前のまま届き、単純な POST（no-cors）では Host は 127.0.0.1 でも Origin に外部の
    ページが付く。Origin はブラウザしか付けないので、無ければ通す（フック、CLI、studio の中継）。
    """
    if host is None or _hostname(host) not in LOCAL_HOSTNAMES:
        return False
    return origin is None or _hostname(urlsplit(origin).netloc) in LOCAL_HOSTNAMES


MAX_TOKENS_LIMIT = 1024


class SummaryRequest(BaseModel):
    system: str
    prompt: str
    # mlx_lm は負の値を無制限とみなす。上限は読み上げ要約の既定（128）に十分な余裕を
    # 持たせた値で、これを超える生成は Runner を塞いでフックの読み上げを待たせるだけ
    max_tokens: StrictInt | None = Field(default=None, ge=1, le=MAX_TOKENS_LIMIT)
    # pairvoice eval が使う。読み上げないので、ミュートを見ず、後発に追い越されても捨てない
    bypass_mute: bool = False
    droppable: bool = True
    # フックが false で送る。初回のダウンロード中は待たずに 503 model_downloading を返す
    wait_download: bool = True


class SamplerOverrides(BaseModel):
    """studio のプレイグラウンドから来るサンプラーの上書き。

    フィールド名は mlx-audio の generate() の引数名そのまま。名前がずれた値は
    黙って捨てられるので、途中で変換しない。extra="forbid" は、キーの打ち間違いを
    422 にして「指定したのに効かない」を起こさないため。

    未指定（None）は「config.toml とモデル既定に任せる」の意味で、そのまま送ると
    config の値を潰してしまうので model_dump(exclude_none=True) で落とす。
    """

    model_config = ConfigDict(extra="forbid")

    num_steps: int | None = None
    cfg_guidance_mode: str | None = None
    cfg_scale_text: float | None = None
    cfg_scale_caption: float | None = None
    cfg_scale_speaker: float | None = None
    t_schedule_mode: str | None = None
    sway_coeff: float | None = None
    duration_scale: float | None = None
    seconds: float | None = None
    rng_seed: int | None = None
    cfg_min_t: float | None = None
    cfg_max_t: float | None = None
    context_kv_cache: bool | None = None
    speaker_kv_scale: float | None = None
    truncation_factor: float | None = None
    rescale_k: float | None = None
    rescale_sigma: float | None = None


class MixPart(BaseModel):
    # データの置き場所からの相対パス（studio が試聴で作った wav）
    audio: str
    # 負の重みは外挿（その声から遠ざかる向きへ伸ばす）。伸ばしすぎると声が崩れるので、
    # 2択が使う幅（負の重みの合計 0.5 まで）に余裕を持たせた範囲に絞る
    weight: float = Field(ge=-1, le=2)


class SpeakRequest(BaseModel):
    text: str
    bypass_mute: bool = False
    # studio からの試聴で使う。指定しなければバックエンドがプロファイルの caption で解決する
    caption: str | None = None
    # studio のプレイグラウンドで使う。指定しなければ config.toml とモデル既定に従う
    sampler: SamplerOverrides | None = None
    # studio のプロファイル作成で使う。プロファイルの参照音声を使わず caption だけで作る
    design: bool = False
    # studio の試聴で使う。使用中でないプロファイルの参照音声で鳴らす
    profile_id: str | None = None
    # 読み上げのフックと `pairvoice say` で使う。合成したら常駐サーバーが鳴らす
    play: bool = False
    # SummaryRequest と同じ。フックが false で送る
    wait_download: bool = True
    # studio の「2択で絞り込む」で使う。もとの声の話者の表現を重みで混ぜた声で鳴らす
    mix: list[MixPart] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _mix_weights_sum_positive(self):
        # 重みは足して1に直して混ぜるので、和が 0 以下では声にならない
        if self.mix is not None and sum(part.weight for part in self.mix) <= 0:
            raise ValueError("mix weights must sum to a positive value")
        return self


class SpeakerVectorRequest(BaseModel):
    audio: str


class MuteRequest(BaseModel):
    # 既定の lax だと true が 1 に化ける。範囲の判定は MuteController に任せる（400）
    minutes: StrictInt | None = Field(default=None)


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
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="pairvoice", lifespan=lifespan)

    @app.middleware("http")
    async def reject_foreign_pages(request: Request, call_next):
        if not is_local_request(request.headers.get("host"), request.headers.get("origin")):
            return JSONResponse(status_code=403, content={"error": "forbidden_origin"})
        return await call_next(request)

    @app.post("/llm")
    async def summarize(request: SummaryRequest):
        try:
            text = await engine.summarize(
                system=request.system,
                prompt=request.prompt,
                max_tokens=request.max_tokens,
                bypass_mute=request.bypass_mute,
                droppable=request.droppable,
                wait_download=request.wait_download,
            )
        except MutedError as error:
            return {"muted": True, "reason": error.reason}
        except Superseded:
            return JSONResponse(status_code=409, content={"dropped": True})
        except ModelUnavailable as error:
            return JSONResponse(
                status_code=503, content={"error": error.code, "detail": error.detail}
            )
        return {"text": text}

    @app.post("/speak")
    async def speak(request: SpeakRequest):
        try:
            result = await engine.speak(
                request.text,
                bypass_mute=request.bypass_mute,
                caption=request.caption,
                sampler=(
                    request.sampler.model_dump(exclude_none=True)
                    if request.sampler is not None
                    else None
                ),
                design=request.design,
                profile_id=request.profile_id,
                play=request.play,
                wait_download=request.wait_download,
                mix=(
                    [(part.audio, part.weight) for part in request.mix]
                    if request.mix is not None
                    else None
                ),
            )
        except MutedError as error:
            return {"muted": True, "reason": error.reason}
        except ProfileNotFound:
            return JSONResponse(status_code=404, content={"error": "profile_not_found"})
        except AudioNotFound:
            return JSONResponse(status_code=404, content={"error": "audio_not_found"})
        except ModelUnavailable as error:
            return JSONResponse(
                status_code=503, content={"error": error.code, "detail": error.detail}
            )
        return {
            "path": str(result.path),
            "relative_path": result.relative_path,
            "duration": result.duration,
        }

    @app.post("/speaker-vector")
    async def speaker_vector(request: SpeakerVectorRequest):
        try:
            vector = await engine.speaker_vector(request.audio)
        except AudioNotFound:
            return JSONResponse(status_code=404, content={"error": "audio_not_found"})
        except ModelUnavailable as error:
            return JSONResponse(
                status_code=503, content={"error": error.code, "detail": error.detail}
            )
        return {"vector": vector}

    @app.get("/health")
    async def health():
        return engine.health()

    @app.post("/warmup", status_code=202)
    async def warmup():
        return await engine.warmup()

    @app.get("/mute")
    async def mute_state():
        return engine.mute.state().describe()

    @app.post("/stop")
    async def stop():
        # 鳴っている読み上げと、待っている読み上げをすべて捨てる
        return {"stopped": engine.player.stop()}

    @app.post("/mute")
    async def mute(request: MuteRequest):
        try:
            state = engine.mute.mute(request.minutes)
        except InvalidMinutes as error:
            return JSONResponse(
                status_code=400,
                content={"error": "invalid_minutes", "detail": str(error)},
            )
        return state.describe()

    @app.post("/unmute")
    async def unmute():
        state = engine.mute.unmute()
        return {"active": state.active}

    return app
