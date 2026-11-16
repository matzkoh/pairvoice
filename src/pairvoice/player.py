"""読み上げの再生。常駐サーバーの中で、1件ずつ順に鳴らす。

鳴らすかどうか（ミュート）を決めるのも、鳴っている音を止めるのも常駐サーバーなので、
再生もここに置く。鳴らす前にミュートを確かめ、鳴っている間も `PlaybackWatch` で見張る
（止めるか音量を下げるかの判断は mute.py が持つ）。

`stop()`（`POST /stop`、メニューバーの「読み上げを止める」）は、鳴っている音と待っている
読み上げに加え、受け付けたがまだ合成中の読み上げも捨てる（`epoch` を受付時に取る）。

鳴らし方は2つある。どちらも実行ループの無いスレッドから鳴り、CoreAudio には常駐サーバー
自身の PID で出るので、ほかのアプリの音と区別できる（`audio_state.AudioProbe` が自分の
PID を除外する）。

- 合成しながら鳴らす（`Stream`）: 合成の側が解けた波形を足していき、届いた分から
  AVAudioEngine に積む。全部を解き終わる前に鳴り始める
- 合成を待って鳴らす（`enqueue`）: 出来上がった wav を AVAudioPlayer で鳴らす
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

import numpy as np

from .config import PlaybackConfig
from .mute import MuteController
from .spatial import soft_clip
from .stall import Stalled, call_with_deadline, start_in_daemon_thread

_log = logging.getLogger(__name__)

# 鳴っている間に見張る間隔。1回の取得は約13ms
WATCH_INTERVAL_SECONDS = 0.25
# 鳴り終わりの近くで見張りを待つ最短の時間。読み上げは文ごとの音を続けて鳴らすので、
# 見張りの間隔ぶん文の間が空かないよう、残りが間隔より短ければ鳴り終わりまでだけ待つ
MIN_WATCH_SECONDS = 0.01
STOP_FADE_SECONDS = 0.15
DUCK_FADE_SECONDS = 0.2
# 鳴らし始めるまでの上限。AVAudioPlayer.play() は CoreAudio が応答しないと戻らない
OPEN_TIMEOUT_SECONDS = 5.0
# 鳴っている音への操作（鳴っているか、音量、停止）の上限。ふだんは即座に戻るが、
# モデルの読み込み中は GIL の取り合いで遅れる（mute.SAMPLE_TIMEOUT_SECONDS と同じ理由）
SOUND_CALL_TIMEOUT_SECONDS = 2.0

# --- 合成しながら鳴らす ---
CHANNELS = 2
# 鳴らし始める前に積んでおく長さ。次の波形が届く前に積んだ分を鳴らし切ると音が途切れる。
# 次が届くまでは約 65ms（M4 Pro でのデコード1チャンク）で、遅い機種や混んでいるときの余裕を見る
START_BUFFER_SECONDS = 0.4
# 最後に足す無音。積んだ分を鳴らし切ったと分かった時点では、末尾がまだデバイスの手前に
# 残っている。その分を無音にして、止めても声の末尾が切れないようにする
END_PAD_SECONDS = 0.1
# 鳴り終わるはずの時刻を過ぎても終わらない音を見切るまでの余裕
END_SLACK_SECONDS = 2.0
# 音量を変えるときの刻み。AVAudioPlayerNode にはフェードが無いので段で変える
RAMP_STEP_SECONDS = 0.01
# 最初の波形を待つ上限。合成が始まってから届くまでは長い文でも数秒
STREAM_START_TIMEOUT_SECONDS = 60.0
# 鳴らし始めた後、次の波形を待つ上限。届く間隔はデコード1チャンクぶんで、1秒に満たない
STREAM_STALL_SECONDS = 10.0


class Sound(Protocol):
    def playing(self) -> bool: ...

    def remaining(self) -> float: ...

    def set_volume(self, volume: float, fade_seconds: float) -> None: ...

    def stop(self) -> None: ...


class StreamSound(Sound, Protocol):
    """波形を足しながら鳴らす音。足すのは (フレーム数, 2) の float32。"""

    def feed(self, pcm: np.ndarray) -> None: ...

    def finish(self) -> None: ...


class Shaper(Protocol):
    """鳴らす直前の加工（spatial.py）。モノラルを受け取り、同じ長さの2ch を返す。"""

    def push(self, samples: np.ndarray) -> np.ndarray: ...

    def flush(self) -> np.ndarray: ...


class _Guarded:
    """CoreAudio への操作。詰まって戻らなければ見切り、鳴り終わったものとみなす。

    Player はイベントループから呼ぶので、固まるとサーバーごと止まる。
    """

    _stalled = False

    def _call(self, fn, *args):
        if self._stalled:
            return None
        try:
            return call_with_deadline(fn, *args, timeout=SOUND_CALL_TIMEOUT_SECONDS)
        except Stalled as stalled:
            # 固まったスレッドはまだ中にいるので、この音にはもう触らない
            self._stalled = True
            _log.warning("再生中の音を操作できません: %s", stalled)
            return None


class _AVSound(_Guarded):
    """AVAudioPlayer で鳴らす wav。"""

    def __init__(self, player) -> None:
        self._player = player
        # 長さは鳴らしている間に変わらないので、見張りのたびに取り直さない
        self._duration: float | None = None

    def playing(self) -> bool:
        return bool(self._call(self._player.isPlaying))

    def remaining(self) -> float:
        if self._duration is None:
            self._duration = self._call(self._player.duration)
        current = self._call(self._player.currentTime)
        if self._duration is None or current is None:
            return WATCH_INTERVAL_SECONDS
        return self._duration - current

    def set_volume(self, volume: float, fade_seconds: float) -> None:
        self._call(self._player.setVolume_fadeDuration_, volume, fade_seconds)

    def stop(self) -> None:
        self._call(self._player.stop)


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


class Output(Protocol):
    """PCM を積んで鳴らす口（AVAudioEngine）。"""

    def schedule(self, pcm: np.ndarray) -> None: ...

    def play(self) -> None: ...

    def frames_played(self) -> int: ...

    def running(self) -> bool: ...

    def set_volume(self, volume: float) -> None: ...

    def stop(self) -> None: ...


class _EngineOutput:
    """AVAudioEngine と AVAudioPlayerNode。AVFoundation に触るのはここだけ（テストで読まない）。

    エンジンは読み上げ1件ごとに作って起こす。出力先は起こした時点の既定のデバイスになるので、
    前の読み上げの後でヘッドホンに切り替えても、次の読み上げから付いていく。
    """

    def __init__(self, sample_rate: int) -> None:
        import AVFoundation

        self._buffer = AVFoundation.AVAudioPCMBuffer
        self._engine = AVFoundation.AVAudioEngine.alloc().init()
        self._node = AVFoundation.AVAudioPlayerNode.alloc().init()
        self._format = (
            AVFoundation.AVAudioFormat.alloc().initStandardFormatWithSampleRate_channels_(
                float(sample_rate), CHANNELS
            )
        )
        self._engine.attachNode_(self._node)
        self._engine.connect_to_format_(self._node, self._engine.mainMixerNode(), self._format)
        self._engine.prepare()
        started, error = self._engine.startAndReturnError_(None)
        if not started:
            raise OSError(f"再生を始められません: {error}")

    def schedule(self, pcm: np.ndarray) -> None:
        frames = len(pcm)
        buffer = self._buffer.alloc().initWithPCMFormat_frameCapacity_(self._format, frames)
        buffer.setFrameLength_(frames)
        for channel, pointer in enumerate(buffer.floatChannelData()):
            np.frombuffer(pointer.as_buffer(frames), dtype=np.float32)[:] = pcm[:, channel]
        self._node.scheduleBuffer_completionHandler_(buffer, None)

    def play(self) -> None:
        self._node.play()

    def frames_played(self) -> int:
        """鳴らし始めてから進んだフレーム数。積んだ分が尽きても進み続ける。"""
        rendered = self._node.lastRenderTime()
        if rendered is None or not rendered.isSampleTimeValid():
            return 0
        position = self._node.playerTimeForNodeTime_(rendered)
        return 0 if position is None else max(0, int(position.sampleTime()))

    def running(self) -> bool:
        return bool(self._engine.isRunning())

    def set_volume(self, volume: float) -> None:
        self._node.setVolume_(volume)

    def stop(self) -> None:
        self._node.stop()
        self._engine.stop()


class _StreamSound(_Guarded):
    """届いた波形を Output に積みながら鳴らす音。

    積んだ分は Python が止まっていても CoreAudio が鳴らすので、届いた波形はすぐ全部積む。
    """

    def __init__(
        self,
        output: Output,
        sample_rate: int,
        volume: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._output = output
        self._rate = sample_rate
        self._clock = clock
        # 積んだ音が鳴り終わる位置（鳴らし始めてからのフレーム数）。積むのが間に合わずに
        # 無音で過ぎた分は、次に積む音がその先から鳴るので、ここに含める
        self._end = 0
        self._started = False
        self._finished = False
        # もう鳴らない（止めた・出力が壊れた・鳴り終わりを見切った）。以後は何もしない
        self._dead = False
        self._released = False
        self._end_by = 0.0
        self._ramp = 0
        self._volume = volume
        self._call(output.set_volume, volume)

    def _call(self, fn, *args):
        if self._dead:
            return None
        try:
            return super()._call(fn, *args)
        except Exception as error:
            # 出力先が変わった後などに AVFoundation が例外を投げる。鳴り終わったものとみなす
            self._dead = True
            _log.warning("再生中の音を操作できません: %s", error)
            return None

    @property
    def _gone(self) -> bool:
        return self._dead or self._stalled

    def feed(self, pcm: np.ndarray) -> None:
        if self._finished or not len(pcm):
            return
        self._schedule(pcm)
        if not self._started and self._end >= START_BUFFER_SECONDS * self._rate:
            self._start()

    def finish(self) -> None:
        """波形はこれで全部。まだ鳴らしていなければ、積んだ分で鳴らし始める。"""
        if self._finished:
            return
        self._finished = True
        if self._end:
            self._schedule(np.zeros((int(END_PAD_SECONDS * self._rate), CHANNELS), np.float32))
            if not self._started:
                self._start()
        self._end_by = self._clock() + self._left() / self._rate + END_SLACK_SECONDS

    def _schedule(self, pcm: np.ndarray) -> None:
        if self._started:
            played = self._call(self._output.frames_played) or 0
            if played > self._end:
                gap = (played - self._end) / self._rate
                _log.warning("波形が間に合わず、音が %.2f 秒途切れました", gap)
                self._end = played
        self._call(self._output.schedule, pcm)
        self._end += len(pcm)

    def _start(self) -> None:
        self._call(self._output.play)
        self._started = True

    def _left(self) -> int:
        """積んだうち、まだ鳴らしていないフレーム数。"""
        if not self._started:
            return self._end
        played = self._call(self._output.frames_played)
        return 0 if played is None else max(0, self._end - played)

    def playing(self) -> bool:
        if self._gone:
            return False
        if not self._finished:
            return True
        if not self._started:
            return False
        running = self._call(self._output.running)
        if self._gone:
            return False
        # 出力先が変わるとエンジンが止まり、再生位置が進まなくなる
        if not running:
            _log.warning("出力が止まったので、読み上げを打ち切りました")
            return False
        if self._clock() > self._end_by:
            _log.warning("鳴り終わらないので、読み上げを打ち切りました")
            return False
        return self._left() > 0

    def remaining(self) -> float:
        # 波形がまだ届くうちは、鳴り終わりは近くない
        if not self._finished:
            return WATCH_INTERVAL_SECONDS
        return self._left() / self._rate

    def set_volume(self, volume: float, fade_seconds: float) -> None:
        self._ramp += 1
        ramp, start, began = self._ramp, self._volume, self._clock()

        def run() -> None:
            # 刻みの数ではなく経過時間で決める。1回の操作にかかる時間が積もっても、時間どおりに終わる
            while (elapsed := self._clock() - began) < fade_seconds:
                if not self._apply(ramp, start + (volume - start) * elapsed / fade_seconds):
                    return
                time.sleep(RAMP_STEP_SECONDS)
            self._apply(ramp, volume)

        # CoreAudio が詰まっても、固まるのはこのスレッドだけ
        threading.Thread(target=run, name="volume-ramp", daemon=True).start()

    def _apply(self, ramp: int, volume: float) -> bool:
        """音量を当てる。後から別の変更が来た後と、鳴り終わった後は当てない。"""
        if ramp != self._ramp or self._dead:
            return False
        try:
            self._output.set_volume(volume)
        except Exception:
            return False
        # 途中で次の変更が来たら、そこから続ける
        self._volume = volume
        return True

    def stop(self) -> None:
        """止めて、エンジンを手放す。鳴り終わった後にも呼ぶ。"""
        if self._released:
            return
        self._released = True
        stalled, self._dead = self._stalled, True
        if not stalled:
            # 固まったスレッドが中にいる出力には触らない
            with contextlib.suppress(Exception):
                call_with_deadline(self._output.stop, timeout=SOUND_CALL_TIMEOUT_SECONDS)


def open_stream_sound(sample_rate: int, volume: float) -> StreamSound:
    """エンジンを起こして、波形を待つ。音は最初の波形を積むまで出ない。"""
    return _StreamSound(_EngineOutput(sample_rate), sample_rate, volume)


def _stop_late_sound(opening: asyncio.Future) -> None:
    """見切った後に鳴り始めた音を止める。見張れない音を鳴らしっぱなしにしない。"""
    if not opening.cancelled() and opening.exception() is None:
        opening.result().stop()


class Stream:
    """合成しながら鳴らす読み上げ1件。合成の側が波形を足し、Player が届いた順に鳴らす。

    足す側（feed / close / abort）はどのスレッドから呼んでもよく、鳴らす側を待たない。
    """

    def __init__(self, sample_rate: int, loop: asyncio.AbstractEventLoop) -> None:
        self.sample_rate = sample_rate
        self._loop = loop
        self._chunks: deque[np.ndarray] = deque()
        self._changed = asyncio.Event()
        self._closed = False
        self._discarded = False
        # 合成に失敗した・取り消された。鳴っていれば止める
        self.aborted = False
        # 保存した wav。合成しながら鳴らせなかったときに、これを鳴らす
        self.wav: Path | None = None

    def feed(self, samples: np.ndarray) -> None:
        """モノラルの波形を足す。"""
        self._post(self._put, samples)

    def close(self, wav: Path | None = None) -> None:
        """波形はこれで全部。wav は保存した音声。"""
        self._post(self._end, wav, False)

    def abort(self) -> None:
        self._post(self._end, None, True)

    def _post(self, fn: Callable, *args) -> None:
        with contextlib.suppress(RuntimeError):  # 戻る前にイベントループが閉じていた
            self._loop.call_soon_threadsafe(fn, *args)

    def _put(self, samples: np.ndarray) -> None:
        if self._closed or self._discarded:
            return
        self._chunks.append(samples)
        self._changed.set()

    def _end(self, wav: Path | None, aborted: bool) -> None:
        if self._closed:
            return
        self._closed = True
        self.wav = wav
        self.aborted = aborted
        self._changed.set()

    def discard(self) -> None:
        """鳴らさないと決めた。届いている波形も、この先届く波形も捨てる。"""
        self._discarded = True
        self._chunks.clear()

    async def take(self) -> np.ndarray | None:
        """次の波形。もう来ないなら None。"""
        while not self._chunks:
            if self._closed:
                return None
            self._changed.clear()
            await self._changed.wait()
        return self._chunks.popleft()

    async def saved(self) -> Path | None:
        """合成が終わるのを待って、保存した wav を返す。無ければ None。届いた波形は捨てる。"""
        while await self.take() is not None:
            pass
        return self.wav


class _Shaping:
    """鳴らす直前の加工を、届いた波形に順に掛けて2ch にする。

    加工できなくなったら、そこから先はそのまま鳴らす。加工の有無によらず、はみ出す山は丸める。
    """

    def __init__(self, shaper: Shaper | None) -> None:
        self._shaper = shaper

    def push(self, samples: np.ndarray) -> np.ndarray:
        if self._shaper is not None:
            try:
                return soft_clip(self._shaper.push(samples))
            except Exception as error:
                self._give_up(error)
        return soft_clip(np.repeat(np.asarray(samples, np.float32)[:, None], CHANNELS, axis=1))

    def flush(self) -> np.ndarray:
        """最後の波形より後ろに続く音（響き）。無ければ長さ 0。"""
        if self._shaper is not None:
            try:
                return soft_clip(self._shaper.flush())
            except Exception as error:
                self._give_up(error)
        return np.zeros((0, CHANNELS), np.float32)

    def _give_up(self, error: Exception) -> None:
        _log.warning("加工できないのでそのまま鳴らします: %s", error)
        self._shaper = None


@dataclass(frozen=True)
class _Job:
    # 鳴らすもの。出来上がった wav か、合成しながら届く波形
    source: Path | Stream
    bypass_mute: bool
    queued_at: float
    epoch: int

    @property
    def name(self) -> str:
        return self.source.name if isinstance(self.source, Path) else "合成しながらの読み上げ"

    @property
    def stream(self) -> Stream | None:
        return self.source if isinstance(self.source, Stream) else None


class Player:
    def __init__(
        self,
        config: PlaybackConfig,
        mute: MuteController,
        opener: Callable[[Path, float], Sound] = open_sound,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        prepare: Callable[[Path], Path | None] | None = None,
        stream_opener: Callable[[int, float], StreamSound] = open_stream_sound,
        shape: Callable[[int], Shaper | None] | None = None,
    ) -> None:
        self._config = config
        # 鳴らす直前に音を加工する（spatial.py）。別のファイルを返せばそれを鳴らし、鳴り終われば消す
        self._prepare = prepare
        # 同じ加工を、合成しながら鳴らす波形に掛けるもの。読み上げ1件ごとに作る
        self._shape = shape
        self._mute = mute
        self._opener = opener
        self._stream_opener = stream_opener
        self._clock = clock
        self._sleep = sleep
        self._queue: deque[_Job] = deque()
        self._ready = asyncio.Event()
        # stop() のたびに進める。古い世代の読み上げは、待っていても鳴っていても捨てる
        self._epoch = 0
        self._playing = False

    def configure(self, config: PlaybackConfig) -> None:
        """読み直した設定を当てる。鳴っている音には、次に音量を変えるときから効く。"""
        self._config = config

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

    def open_stream(
        self, sample_rate: int, *, bypass_mute: bool = False, epoch: int | None = None
    ) -> Stream:
        """合成しながら鳴らす読み上げを列に積む。合成の側は、返した Stream に波形を足していく。

        イベントループの上で呼ぶ。列の順番は積んだ時点で決まり、波形が届く前に順番が来たら届くのを待つ。
        """
        stream = Stream(sample_rate, asyncio.get_running_loop())
        epoch = self._epoch if epoch is None else epoch
        if epoch != self._epoch:
            stream.discard()
            return stream
        self._queue.append(_Job(stream, bypass_mute, self._clock(), epoch))
        self._ready.set()
        return stream

    def stop(self) -> int:
        """鳴っている音と待っている読み上げを捨て、捨てた件数を返す。"""
        dropped = len(self._queue) + (1 if self._playing else 0)
        for job in self._queue:
            if job.stream is not None:
                job.stream.discard()
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
                _log.warning("再生に失敗しました（%s）: %s", job.name, error)
            finally:
                # どの道筋で抜けても、この先届く波形は溜めない
                if job.stream is not None:
                    job.stream.discard()

    async def _handle(self, job: _Job) -> None:
        if self._clock() - job.queued_at > self._config.max_wait_seconds:
            _log.info("待ちすぎたので鳴らしません: %s", job.name)
            return
        source = job.source
        if isinstance(source, Stream):
            await self._play_stream(job, source)
        elif await self._may_play(job):
            await self._play(job, source)

    async def _may_play(self, job: _Job) -> bool:
        """鳴らす直前の確認。ミュートと、受け付けた後の「止める」。"""
        if not job.bypass_mute:
            # 通知音のような短い音なら止むのを待つ
            state = await self._mute.wait_state()
            if state.active:
                _log.info("ミュート中なので鳴らしません（%s）: %s", state.reason, job.name)
                return False
        return not self._cancelled(job)

    def _cancelled(self, job: _Job) -> bool:
        return job.epoch != self._epoch or (job.stream is not None and job.stream.aborted)

    async def _opened(self, name: str, opening: asyncio.Future):
        """別スレッドで開いている音を待つ。CoreAudio が応答しなければ見切る。

        イベントループで開くと、CoreAudio が詰まったときに /health も含めてサーバーごと固まる。
        固まったスレッドは取り消せないので、見切った後に戻ってきた音は止める。
        """
        try:
            await asyncio.wait({opening}, timeout=OPEN_TIMEOUT_SECONDS)
        finally:
            # 見切ったときも、待つ途中で取り消されたときも、後から鳴り始めた音を止める
            if not opening.done():
                opening.add_done_callback(_stop_late_sound)
        if not opening.done():
            raise OSError(f"CoreAudio が応答しないので鳴らせません: {name}")
        return opening.result()

    async def _prepared(self, path: Path) -> Path | None:
        """加工した音の場所。加工しないとき・できなかったときは None（元の音を鳴らす）。"""
        if self._prepare is None:
            return None
        try:
            return await asyncio.to_thread(self._prepare, path)
        except Exception as error:
            _log.warning("加工できないのでそのまま鳴らします（%s）: %s", path.name, error)
            return None

    async def _play(self, job: _Job, path: Path) -> None:
        prepared = await self._prepared(path)
        try:
            opening = start_in_daemon_thread(self._opener, prepared or path, self._config.volume)
            await self._watch(job, await self._opened(job.name, opening))
        finally:
            if prepared is not None:
                prepared.unlink(missing_ok=True)

    async def _play_stream(self, job: _Job, stream: Stream) -> None:
        # エンジンの起動（数百ミリ秒）は、最初の波形を待つ間に済ませる
        opening = start_in_daemon_thread(
            self._stream_opener, stream.sample_rate, self._config.volume
        )
        sound: StreamSound | None = None
        try:
            first = await self._first_wave(job, stream)
            if first is None or not await self._may_play(job):
                return
            try:
                sound = await self._opened(job.name, opening)
            except Exception as error:
                _log.warning("合成しながら鳴らせないので、合成を待って鳴らします: %s", error)
                await self._play_saved(job, stream)
                return
            await self._watch_fed(job, stream, first, sound)
        finally:
            if sound is not None:
                sound.stop()
            else:
                # 鳴らさないと決めた。開いている途中でも、もう開いていても、開いた音は止める
                opening.add_done_callback(_stop_late_sound)

    async def _first_wave(self, job: _Job, stream: Stream) -> np.ndarray | None:
        """最初の波形。届かないか、1つも無いまま終わった（合成の失敗か取り消し）なら None。"""
        try:
            async with asyncio.timeout(STREAM_START_TIMEOUT_SECONDS):
                return await stream.take()
        except TimeoutError:
            _log.warning("波形が届かないので鳴らしません: %s", job.name)
            return None

    async def _play_saved(self, job: _Job, stream: Stream) -> None:
        """合成しながら鳴らせなかった読み上げを、保存した wav で鳴らす。"""
        async with asyncio.timeout(STREAM_START_TIMEOUT_SECONDS):
            wav = await stream.saved()
        if wav is not None and not self._cancelled(job):
            await self._play(job, wav)

    async def _watch_fed(
        self, job: _Job, stream: Stream, first: np.ndarray, sound: StreamSound
    ) -> None:
        """届く波形を音に足しながら、鳴り終わるまで見張る。"""
        feeding = asyncio.create_task(self._feed(stream, first, sound))
        try:
            await self._watch(job, sound)
        finally:
            feeding.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feeding

    def _shaper(self, sample_rate: int) -> Shaper | None:
        """届いた波形に掛ける加工。加工しないとき・用意できなかったときは None。"""
        if self._shape is None:
            return None
        try:
            return self._shape(sample_rate)
        except Exception as error:
            _log.warning("加工できないのでそのまま鳴らします: %s", error)
            return None

    async def _feed(self, stream: Stream, first: np.ndarray, sound: StreamSound) -> None:
        """届いた波形を、加工して音に足していく。"""
        try:
            shaping = _Shaping(self._shaper(stream.sample_rate))
            chunk: np.ndarray | None = first
            while chunk is not None:
                sound.feed(await asyncio.to_thread(shaping.push, chunk))
                async with asyncio.timeout(STREAM_STALL_SECONDS):
                    chunk = await stream.take()
            tail = await asyncio.to_thread(shaping.flush)
            if len(tail):
                sound.feed(tail)
        except TimeoutError:
            _log.warning("波形が途中で届かなくなりました")
        except Exception as error:
            _log.warning("波形を足せませんでした: %s", error)
        finally:
            sound.finish()

    async def _watch(self, job: _Job, sound: Sound) -> None:
        """鳴り終わるまで見張る。ミュートや「止める」が入ったら止め、ほかのアプリの音には音量を下げる。"""
        volume = self._config.volume
        watch = self._mute.watch(bypass_mute=job.bypass_mute)
        self._playing = True
        ducked = False
        try:
            while sound.playing():
                if self._cancelled(job):
                    reason, duck = "stop", False
                else:
                    reason, duck = await asyncio.to_thread(watch.check)
                if reason is not None:
                    sound.set_volume(0.0, STOP_FADE_SECONDS)
                    await self._sleep(STOP_FADE_SECONDS)
                    sound.stop()
                    _log.info("止めました（%s）: %s", reason, job.name)
                    return
                if duck != ducked:
                    sound.set_volume(
                        self._config.duck_volume if duck else volume, DUCK_FADE_SECONDS
                    )
                    ducked = duck
                await self._sleep(
                    min(WATCH_INTERVAL_SECONDS, max(sound.remaining(), MIN_WATCH_SECONDS))
                )
        finally:
            self._playing = False
