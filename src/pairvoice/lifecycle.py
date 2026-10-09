"""モデルの状態機械と、生成の直列実行。

MLX は同時実行に弱いので、生成も解放もグローバルに1本のキューで順に処理する。
キューが順番を、専用の1本のスレッド（_MLX_EXECUTOR）が実行する場所を揃える。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from . import generations
from .player import Player

_log = logging.getLogger(__name__)

DROP_WINDOW_SECONDS = 300.0
MAINTENANCE_INTERVAL_SECONDS = 30.0
PRUNE_INTERVAL_SECONDS = 86400.0

# MLX のストリームはスレッドごとに持たれる。モデルを読み込んだスレッドと別のスレッドで
# 生成すると「There is no Stream(gpu, N) in current thread」で落ちる。asyncio.to_thread の
# 既定プールは複数のワーカーを持ち、どれに当たるかは呼ぶたびに変わるので、読み込み・
# 生成・解放はすべてこの1本で実行する（ダウンロードは MLX を触らないので対象外）
_MLX_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mlx")


async def on_mlx_thread(fn: Callable, *args):
    return await asyncio.get_running_loop().run_in_executor(_MLX_EXECUTOR, fn, *args)


def limit_mlx_cache() -> None:
    """常駐サーバーと `pairvoice eval --model` が、同じメモリの条件で MLX を動かすための設定。"""
    import mlx.core as mx

    # MLX は解放したバッファを再利用のためにプロセス内に溜め、既定では上限が実質無い。
    # 常駐中ずっと抱えたままになる（TTS の合成後に最大 2.7GB）ので、すぐ OS に返させる。
    # 要約・合成とも 1 回が秒単位で、確保し直す分の遅れは計測で誤差に埋もれた
    mx.set_cache_limit(0)


class ModelState(StrEnum):
    UNLOADED = "unloaded"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    LOADED = "loaded"
    FAILED = "failed"
    MISCONFIGURED = "misconfigured"


# warmup が読み込みを始める状態。メニューの事前ロードもこれで押せるかを決める
WARMABLE_STATES = frozenset({ModelState.UNLOADED, ModelState.FAILED})


class Backend(Protocol):
    name: str
    model: str

    def preflight(self) -> str | None: ...
    def is_downloaded(self) -> bool: ...
    def download(self) -> None: ...
    def load(self) -> None: ...
    def unload(self) -> None: ...


class ModelUnavailable(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class Superseded(Exception):
    """後発の要求に追い越されて捨てられた。"""


def _consume_exception(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class ManagedModel:
    def __init__(
        self,
        backend: Backend,
        idle_unload_seconds: float,
        load_timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._idle_unload_seconds = idle_unload_seconds
        self._load_timeout_seconds = load_timeout_seconds
        self._clock = clock
        self._state = ModelState.UNLOADED
        self._detail = ""
        self._loading: asyncio.Task | None = None
        # ロードのタイムアウトはダウンロードを含めず、LOADING に入ってから数える。
        # 期限は _load() が LOADING に入るときに決め、Event で待っている側に知らせる
        self._loading_started = asyncio.Event()
        self._loading_deadline = 0.0
        self._loaded_at: float | None = None
        self._last_used: float | None = None

    async def ensure_loaded(self, wait_download: bool = True) -> None:
        """wait_download=False なら、ダウンロードを始める（続ける）だけで待たずに断る。

        フックはタイムアウトの短い要求で呼ぶので、初回の数 GB のダウンロードを待つと
        打ち切られて理由が分からなくなる。ダウンロードは背景で続き、次の要求が合流する。
        """
        problem = self._backend.preflight()
        if problem:
            # DOWNLOADING / LOADING / LOADED はロード経路が持つ状態で、重みは載っている
            # （載りつつある）。ここで MISCONFIGURED に書き換えると unload_if_idle が
            # 解放せず、refresh_preflight が unloaded と申告して二重に読み込む
            if self._state in (ModelState.UNLOADED, ModelState.MISCONFIGURED, ModelState.FAILED):
                self._state = ModelState.MISCONFIGURED
                self._detail = problem
            elif self._state is ModelState.LOADED:
                # 状態は重みに合わせて loaded のまま、503 になる理由を detail で見せる
                self._detail = problem
            raise ModelUnavailable(problem, f"{self._backend.name}: 設定を確認してください")

        if self._state is ModelState.LOADED:
            self._detail = ""
            return

        if self._loading is None or self._loading.done():
            self._loading_started = asyncio.Event()
            self._loading = asyncio.create_task(self._load())
        task = self._loading

        # _loading_started が立つまでは、ダウンロード中かこれから始まる（タスクがまだ走っていない）
        if (
            not wait_download
            and not self._loading_started.is_set()
            and (self._state is ModelState.DOWNLOADING or not self._backend.is_downloaded())
        ):
            # 待たないので、背景のタスクの失敗はここでは受け取れない。state と detail に残る
            task.add_done_callback(_consume_exception)
            raise ModelUnavailable(
                "model_downloading", f"{self._backend.name}: モデルをダウンロードしています"
            )

        # asyncio.wait は待つのをやめても task を取り消さない。タイムアウトで呼び出し側に
        # 返した後もロードは背景で続き、後から来た呼び出しがそれに合流する
        started = asyncio.ensure_future(self._loading_started.wait())
        try:
            await asyncio.wait({task, started}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            started.cancel()
        if not task.done():
            await asyncio.wait({task}, timeout=max(self._loading_deadline - self._clock(), 0))
        if not task.done():
            # state は LOADING のまま残す。ロードはまだ走っているので、ここで FAILED に
            # すると snapshot() が嘘をつき、後から合流した呼び出しもタイムアウトを
            # 数えられなくなる
            self._detail = "load timeout"
            raise ModelUnavailable("model_load_failed", "ロードがタイムアウトしました")

        error = task.exception()
        if error is not None:
            self._state = ModelState.FAILED
            self._detail = str(error)
            raise ModelUnavailable("model_load_failed", str(error))

    async def _load(self) -> None:
        try:
            if not self._backend.is_downloaded():
                self._state = ModelState.DOWNLOADING
                await asyncio.to_thread(self._backend.download)
            self._state = ModelState.LOADING
            self._loading_deadline = self._clock() + self._load_timeout_seconds
            self._loading_started.set()
            await on_mlx_thread(self._backend.load)
        except BaseException:
            self._state = ModelState.FAILED
            raise
        self._state = ModelState.LOADED
        self._detail = ""
        self._loaded_at = self._clock()

    def touch(self) -> None:
        self._last_used = self._clock()

    async def unload_if_idle(self) -> bool:
        if self._state is not ModelState.LOADED:
            return False
        reference = max(filter(None, (self._loaded_at, self._last_used)), default=None)
        if reference is None:
            return False
        if self._clock() - reference <= self._idle_unload_seconds:
            return False
        await on_mlx_thread(self._backend.unload)
        self._state = ModelState.UNLOADED
        self._loaded_at = None
        return True

    def refresh_preflight(self) -> None:
        """UNLOADED / MISCONFIGURED の間は preflight() を再評価し、状態に反映する。

        DOWNLOADING / LOADING / LOADED / FAILED はロード経路が所有する状態なので、
        状態は触らない（LOADED を preflight 差し戻しで誤って misconfigured に
        したり、LOADING を横から書き換えたりしないため）。LOADED だけは、要求が
        503 になる理由を detail に載せる（重みは載ったまま、使えない）。
        """
        if self._state is ModelState.LOADED:
            self._detail = self._backend.preflight() or ""
            return
        if self._state not in (ModelState.UNLOADED, ModelState.MISCONFIGURED):
            return
        problem = self._backend.preflight()
        if problem:
            self._state = ModelState.MISCONFIGURED
            self._detail = problem
        else:
            self._state = ModelState.UNLOADED
            self._detail = ""

    def snapshot(self) -> dict:
        return {
            "model": self._backend.model,
            "state": self._state.value,
            "last_used": self._last_used,
            "detail": self._detail,
        }


class Runner:
    """実行中1件のキュー。捨てられるのは待機中の droppable な要求だけ。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._pending: object | None = None
        self._superseded: set[object] = set()
        self._drops: list[float] = []
        self.running = 0
        self.waiting = 0

    async def run(self, fn: Callable, droppable: bool):
        token = object()
        if droppable:
            if self._pending is not None:
                self._superseded.add(self._pending)
            self._pending = token

        self.waiting += 1
        acquired = False
        try:
            await self._lock.acquire()
            acquired = True
            self.waiting -= 1

            if token in self._superseded:
                self._superseded.discard(token)
                self._drops.append(self._clock())
                raise Superseded

            if self._pending is token:
                self._pending = None

            self.running += 1
            try:
                return await fn()
            finally:
                self.running -= 1
        finally:
            # 外部キャンセル（クライアント切断等）を含め、どの経路で抜けても
            # 自分の token を後片付けする。これをしないと、待機中に
            # キャンセルされた droppable な呼び出しが _superseded / _pending に
            # 残り続け、誰にも回収されない残骸になる。
            self._superseded.discard(token)
            if self._pending is token:
                self._pending = None
            if acquired:
                self._lock.release()
            else:
                self.waiting -= 1

    def dropped_recent(self, window: float = DROP_WINDOW_SECONDS) -> int:
        cutoff = self._clock() - window
        self._drops = [at for at in self._drops if at >= cutoff]
        return len(self._drops)


class MutedError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Engine:
    """設定・バックエンド・ミュート判定・直列キューを束ねる。"""

    def __init__(
        self,
        config,
        llm_backend,
        tts_backend,
        mute,
        clock: Callable[[], float] = time.monotonic,
        player: Player | None = None,
    ) -> None:
        self._config = config
        self._llm_backend = llm_backend
        self._tts_backend = tts_backend
        self.mute = mute
        self.player = player or Player(config.playback, mute)
        self._clock = clock
        self._runner = Runner(clock=clock)
        self._llm = ManagedModel(
            backend=llm_backend,
            idle_unload_seconds=config.llm.idle_unload_seconds,
            load_timeout_seconds=config.llm.load_timeout_seconds,
            clock=clock,
        )
        self._tts = ManagedModel(
            backend=tts_backend,
            idle_unload_seconds=config.tts.idle_unload_seconds,
            load_timeout_seconds=config.tts.load_timeout_seconds,
            clock=clock,
        )
        self._tasks: list[asyncio.Task] = []
        self._last_prune: float | None = None

    @staticmethod
    def _raise_if_muted(state) -> None:
        if state.active:
            raise MutedError(state.reason or "manual")

    async def _run_unless_muted(self, fn: Callable, *, bypass_mute: bool, droppable: bool):
        """ミュートを、キューに入れる前と Runner で実行する直前の2回確かめてから fn を実行する。"""
        if not bypass_mute:
            # 通知音のような短い音なら止むのを待つ。キューの外なので Runner は塞がない
            self._raise_if_muted(await self.mute.wait_state())

        async def work():
            # キューで待っている間にミュートが有効化されうるので、実行直前にもう一度判定する。
            # 投入前のチェックは早期に返せる分だけ得なので残す
            if not bypass_mute:
                # ここで音が止むのを待つと Runner を塞ぐので、ほかのアプリの音は見ない。
                # 音は Player が鳴らす直前に確かめる
                self._raise_if_muted(self.mute.state(include_output=False))
            return await fn()

        return await self._runner.run(work, droppable=droppable)

    async def summarize(
        self,
        system: str,
        prompt: str,
        max_tokens: int | None = None,
        bypass_mute: bool = False,
        droppable: bool = True,
        wait_download: bool = True,
    ) -> str:
        """droppable なら、待っている間に後発の要約が来たら捨てる（もう読み上げる意味が無い）。

        読み上げに使わない要約（`pairvoice eval`）は bypass_mute と droppable=False で呼ぶ。
        """

        async def work():
            await self._llm.ensure_loaded(wait_download=wait_download)
            text = await on_mlx_thread(self._llm_backend.generate, system, prompt, max_tokens)
            self._llm.touch()
            return text

        return await self._run_unless_muted(work, bypass_mute=bypass_mute, droppable=droppable)

    async def speak(
        self,
        text: str,
        bypass_mute: bool = False,
        caption: str | None = None,
        sampler: dict | None = None,
        design: bool = False,
        profile_id: str | None = None,
        play: bool = False,
        wait_download: bool = True,
        mix: list[tuple[str, float]] | None = None,
        style: str | None = None,
    ):
        """play なら、合成した音声を Player の列に積んでから返す（鳴り終わるのは待たない）。"""
        # 合成を待っている間に「止める」が押されたら、出来上がっても鳴らさない
        epoch = self.player.epoch

        async def work():
            await self._tts.ensure_loaded(wait_download=wait_download)
            resolved_mix = (
                [(self._tts_backend.resolve_audio(audio), weight) for audio, weight in mix]
                if mix
                else None
            )
            result = await on_mlx_thread(
                self._tts_backend.speak,
                text,
                caption,
                sampler,
                design,
                profile_id,
                resolved_mix,
                style,
            )
            self._tts.touch()
            return result

        result = await self._run_unless_muted(work, bypass_mute=bypass_mute, droppable=False)
        if play:
            self.player.enqueue(result.path, bypass_mute=bypass_mute, epoch=epoch)
        return result

    def list_profiles(self) -> dict:
        return self._tts_backend.list_profiles()

    def list_styles(self) -> list[dict]:
        return self._tts_backend.list_styles()

    async def speaker_vector(self, audio: str) -> list[float]:
        """データの置き場所の wav の話者ベクトル。2択でもとの声どうしの位置を測る。"""
        path = self._tts_backend.resolve_audio(audio)

        async def work():
            await self._tts.ensure_loaded()
            vector = await on_mlx_thread(self._tts_backend.speaker_vector, path)
            self._tts.touch()
            return vector

        # 測るのは studio の操作で、音は出さないのでミュートは効かせない
        return await self._run_unless_muted(work, bypass_mute=True, droppable=False)

    async def warmup(self) -> dict:
        # ロードも直列キューを通し、llm と tts を順に読み込む（並行だとピークメモリが両方の合計になる）
        started, already = [], []
        for name, model in (("llm", self._llm), ("tts", self._tts)):
            if model.snapshot()["state"] in WARMABLE_STATES:
                started.append(name)
            else:
                already.append(name)
        for name, model in (("llm", self._llm), ("tts", self._tts)):
            try:
                await self._runner.run(model.ensure_loaded, droppable=False)
            except Exception as error:
                # 失敗の中身は /health の state と detail にも残る
                _log.warning("%s の読み込みに失敗しました: %s", name, error)
                if name in started:
                    started.remove(name)
        return {"started": started, "already": already}

    def health(self) -> dict:
        from .config import config_is_stale

        # unloaded / misconfigured の間は毎回 preflight を取り直す。一度もロードしていない
        # 起動直後でも、参照音声の欠落などを health() の時点で見せるため
        self._llm.refresh_preflight()
        self._tts.refresh_preflight()

        mute_state = self.mute.state()
        # caption は studio が「いま動いている版」を初期表示するために要る。
        # caption.txt が無いときの既定値は設定の側にあり、studio はそれを知らない。
        caption, caption_source = self._tts_backend.resolve_caption()
        return {
            "ok": True,
            "llm": self._describe(self._llm),
            "tts": {
                **self._describe(self._tts),
                "caption": caption,
                "caption_source": caption_source,
                # 使用中のプロファイル。bootstrap 前（まだ一度も合成していない）は None
                "profile": self._tts_backend.describe_profile(),
                # studio のプロファイル作成が候補に読ませる文の初期値。既定の声を自動で作る
                # ときと同じ文にそろえるため、正本はバックエンドの1か所に置く
                "anchor_text": self._tts_backend.anchor_text,
                # caption と同じ理由。studio のプレイグラウンドが「いま渡している値」を
                # 初期値にするために要る。モデル既定は studio 側の表が持つ
                "sampler": self._tts_backend.resolve_sampler(),
            },
            "mute": mute_state.describe(),
            "queue": {"running": self._runner.running, "waiting": self._runner.waiting},
            "playback": self.player.describe(),
            "dropped_recent": self._runner.dropped_recent(),
            "config_stale": config_is_stale(self._config),
        }

    def _describe(self, model: ManagedModel) -> dict:
        snapshot = model.snapshot()
        last_used = snapshot.pop("last_used")
        if last_used is not None:
            offset = self._clock() - last_used
            snapshot["last_used"] = (
                datetime.fromtimestamp(time.time() - offset).astimezone().isoformat()
            )
        else:
            snapshot["last_used"] = None
        return snapshot

    async def maintain(self) -> None:
        for model in (self._llm, self._tts):
            await self._runner.run(model.unload_if_idle, droppable=False)
        # 合成音声の掃除は MLX を使わないので Runner を通さない。1日1回で足りる
        if self._last_prune is None or self._clock() - self._last_prune >= PRUNE_INTERVAL_SECONDS:
            self._last_prune = self._clock()
            tts = self._config.tts
            await asyncio.to_thread(generations.prune, tts.output_dir, tts.output_max_age_days)

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._maintain_loop()),
            asyncio.create_task(self.player.run()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []

    async def _maintain_loop(self) -> None:
        while True:
            await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)
            try:
                await self.maintain()
            except Exception as error:
                _log.warning("maintain に失敗しました: %s", error)
