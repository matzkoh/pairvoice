"""読み上げの再生。常駐サーバーの中で、1件ずつ順に鳴らす。

鳴らすかどうか（ミュート）を決めるのも、鳴っている音を止めるのも常駐サーバーなので、
再生もここに置く。鳴らす前にミュートを確かめ、鳴っている間も `PlaybackWatch` で見張る
（止めるか音量を下げるかの判断は mute.py が持つ）。

`stop()`（`POST /stop`、メニューバーの「読み上げを止める」）は、鳴っている音と待っている
読み上げに加え、受け付けたがまだ合成中の読み上げも捨てる（`epoch` を受付時に取る）。

音は AVFoundation の AVAudioPlayer で鳴らす。実行ループの無いスレッドからでも鳴り、
CoreAudio には常駐サーバー自身の PID で出るので、ほかのアプリの音と区別できる
（`audio_state.AudioProbe` が自分の PID を除外する）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import PlaybackConfig
from .mute import MuteController

_log = logging.getLogger(__name__)

# 鳴っている間に見張る間隔。1回の取得は約13ms
WATCH_INTERVAL_SECONDS = 0.25
STOP_FADE_SECONDS = 0.15
DUCK_FADE_SECONDS = 0.2
# 鳴らし始めるまでの上限。AVAudioPlayer.play() は CoreAudio が応答しないと戻らない
OPEN_TIMEOUT_SECONDS = 5.0


class Sound(Protocol):
    def playing(self) -> bool: ...

    def set_volume(self, volume: float, fade_seconds: float) -> None: ...

    def stop(self) -> None: ...


class _AVSound:
    def __init__(self, player) -> None:
        self._player = player

    def playing(self) -> bool:
        return bool(self._player.isPlaying())

    def set_volume(self, volume: float, fade_seconds: float) -> None:
        self._player.setVolume_fadeDuration_(volume, fade_seconds)

    def stop(self) -> None:
        self._player.stop()


def open_sound(path: Path, volume: float) -> Sound:
    """AVAudioPlayer で鳴らし始める。AVFoundation の import はここだけで行う（テストで読まない）。"""
    import AVFoundation
    import Foundation

    url = Foundation.NSURL.fileURLWithPath_(str(path))
    player, error = AVFoundation.AVAudioPlayer.alloc().initWithContentsOfURL_error_(url, None)
    if player is None:
        raise OSError(f"再生できません: {path}: {error}")
    player.setVolume_(volume)
    if not player.play():
        raise OSError(f"再生を始められません: {path}")
    return _AVSound(player)


def _in_daemon_thread(fn: Callable[..., Sound], *args) -> asyncio.Future:
    """固まりうる呼び出しを使い捨ての daemon スレッドで走らせる。

    asyncio.to_thread の共有プールで固まると、ミュートの判定やダウンロードと取り合いになり、
    終了時の join でサーバーの停止まで止まる。
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def settle(result, error) -> None:
        if future.cancelled():
            if result is not None:
                result.stop()
        elif error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def run() -> None:
        try:
            result, error = fn(*args), None
        except Exception as failed:
            result, error = None, failed
        with contextlib.suppress(RuntimeError):  # 戻る前にイベントループが閉じていた
            loop.call_soon_threadsafe(settle, result, error)

    threading.Thread(target=run, name="audio-open", daemon=True).start()
    return future


def _stop_late_sound(opening: asyncio.Future) -> None:
    """見切った後に鳴り始めた音を止める。見張れない音を鳴らしっぱなしにしない。"""
    if not opening.cancelled() and opening.exception() is None:
        opening.result().stop()


@dataclass(frozen=True)
class _Job:
    path: Path
    bypass_mute: bool
    queued_at: float
    epoch: int


class Player:
    def __init__(
        self,
        config: PlaybackConfig,
        mute: MuteController,
        opener: Callable[[Path, float], Sound] = open_sound,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._mute = mute
        self._opener = opener
        self._clock = clock
        self._sleep = sleep
        self._queue: deque[_Job] = deque()
        self._ready = asyncio.Event()
        # stop() のたびに進める。古い世代の読み上げは、待っていても鳴っていても捨てる
        self._epoch = 0
        self._playing = False

    @property
    def epoch(self) -> int:
        """読み上げを受け付けた時点で取り、enqueue() に渡す。間に stop() があれば鳴らさない。"""
        return self._epoch

    def enqueue(self, path: Path, *, bypass_mute: bool = False, epoch: int | None = None) -> None:
        epoch = self._epoch if epoch is None else epoch
        if epoch != self._epoch:
            return
        self._queue.append(_Job(path, bypass_mute, self._clock(), epoch))
        self._ready.set()

    def stop(self) -> int:
        """鳴っている音と待っている読み上げを捨て、捨てた件数を返す。"""
        dropped = len(self._queue) + (1 if self._playing else 0)
        self._queue.clear()
        self._epoch += 1
        return dropped

    def describe(self) -> dict:
        return {"playing": self._playing, "waiting": len(self._queue)}

    async def run(self) -> None:
        while True:
            if not self._queue:
                self._ready.clear()
                await self._ready.wait()
                continue
            job = self._queue.popleft()
            try:
                await self._handle(job)
            except Exception as error:
                _log.warning("再生に失敗しました（%s）: %s", job.path.name, error)

    async def _handle(self, job: _Job) -> None:
        if self._clock() - job.queued_at > self._config.max_wait_seconds:
            _log.info("待ちすぎたので鳴らしません: %s", job.path.name)
            return
        if not job.bypass_mute:
            # 通知音のような短い音なら止むのを待つ
            state = await self._mute.wait_state()
            if state.active:
                _log.info("ミュート中なので鳴らしません（%s）: %s", state.reason, job.path.name)
                return
        if job.epoch != self._epoch:
            return
        await self._play(job)

    async def _open(self, path: Path, volume: float) -> Sound:
        # イベントループで呼ぶと、CoreAudio が詰まったときに /health も含めてサーバーごと固まる。
        # 別スレッドで呼んで見切る。固まったスレッドは取り消せないので、戻ってきたら止める
        opening = _in_daemon_thread(self._opener, path, volume)
        try:
            await asyncio.wait({opening}, timeout=OPEN_TIMEOUT_SECONDS)
        finally:
            # 見切ったときも、待つ途中で取り消されたときも、後から鳴り始めた音を止める
            if not opening.done():
                opening.add_done_callback(_stop_late_sound)
        if not opening.done():
            raise OSError(f"CoreAudio が応答しないので鳴らせません: {path.name}")
        return opening.result()

    async def _play(self, job: _Job) -> None:
        volume = self._config.volume
        watch = self._mute.watch(bypass_mute=job.bypass_mute)
        sound = await self._open(job.path, volume)
        self._playing = True
        ducked = False
        try:
            while sound.playing():
                if job.epoch != self._epoch:
                    reason, duck = "stop", False
                else:
                    reason, duck = await asyncio.to_thread(watch.check)
                if reason is not None:
                    sound.set_volume(0.0, STOP_FADE_SECONDS)
                    await self._sleep(STOP_FADE_SECONDS)
                    sound.stop()
                    _log.info("止めました（%s）: %s", reason, job.path.name)
                    return
                if duck != ducked:
                    sound.set_volume(
                        self._config.duck_volume if duck else volume, DUCK_FADE_SECONDS
                    )
                    ducked = duck
                await self._sleep(WATCH_INTERVAL_SECONDS)
        finally:
            self._playing = False
