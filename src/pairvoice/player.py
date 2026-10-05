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
import logging
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

    async def _play(self, job: _Job) -> None:
        volume = self._config.volume
        watch = self._mute.watch(bypass_mute=job.bypass_mute)
        sound = self._opener(job.path, volume)
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
