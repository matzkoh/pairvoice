"""ミュート判定。明示ミュートと自動条件の論理和で決める。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from .config import MuteConfig
from .stall import Stalled, call_with_deadline

MIN_MINUTES = 1
MAX_MINUTES = 480
# 再生が止むのを待つ間に取り直す間隔。1回の取得は約13ms
WAIT_POLL_SECONDS = 0.5
# 1回の取得の上限。state() はイベントループからも呼ぶ（/health、キューを抜けた直後の判定）ので、
# CoreAudio が詰まって戻らないとサーバーごと止まる。ふだんは約 10ms だが、モデルの読み込み中は
# GIL を取り合って 1 秒を超えることがある
SAMPLE_TIMEOUT_SECONDS = 2.0

_log = logging.getLogger(__name__)


class InvalidMinutes(ValueError):
    """明示ミュートの分数が範囲外。無期限のミュートは受け付けない。"""


@dataclass(frozen=True)
class MuteState:
    active: bool
    reason: str | None
    until: datetime | None

    def describe(self) -> dict:
        return {
            "active": self.active,
            "reason": self.reason,
            "until": self.until.isoformat() if self.until else None,
        }


_OFF = MuteState(active=False, reason=None, until=None)


class MuteController:
    def __init__(
        self,
        config: MuteConfig,
        probe,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._probe = probe
        # 壁時計は手動ミュートの期限（until として見せる時刻）にだけ使う。待ち時間や
        # 鳴り続けた時間は、時計合わせで伸び縮みしないよう単調時計で数える
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._manual_until: float | None = None
        # 戻らなくなった取得のスレッド。戻るまでは次を呼ばず、最後に取れた結果を使う
        self._stalled_sample = None
        self._last_activity = None

    def mute(self, minutes) -> MuteState:
        if not isinstance(minutes, int) or isinstance(minutes, bool):
            raise InvalidMinutes(f"{MIN_MINUTES}〜{MAX_MINUTES} の整数を指定してください")
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise InvalidMinutes(f"{MIN_MINUTES}〜{MAX_MINUTES} の整数を指定してください")
        self._manual_until = self._clock() + minutes * 60
        return self.state()

    def unmute(self) -> MuteState:
        self._manual_until = None
        return self.state()

    def state(self, include_output: bool = True) -> MuteState:
        manual = self._manual_state()
        if manual is not None:
            return manual

        check_output = include_output and self._config.auto_audio_output
        # 自動条件がどれも切れていれば、CoreAudio を取りに行かない
        if not (self._config.auto_microphone or check_output):
            return _OFF
        activity = self._sample()
        if activity is None:
            return _OFF

        if self._config.auto_microphone and activity.microphone:
            return MuteState(active=True, reason="microphone", until=None)
        if check_output and activity.output:
            return MuteState(active=True, reason="audio_output", until=None)
        return _OFF

    def watch(self, *, bypass_mute: bool = False) -> PlaybackWatch:
        """鳴っている間の見張りを1件分作る。"""
        return PlaybackWatch(self, bypass_mute=bypass_mute)

    def _manual_state(self) -> MuteState | None:
        if self._manual_until is None:
            return None
        if self._clock() < self._manual_until:
            until = datetime.fromtimestamp(self._manual_until).astimezone()
            return MuteState(active=True, reason="manual", until=until)
        self._manual_until = None
        return None

    def _sample(self):
        # 見切ったときに「取れない（鳴らす）」に倒すと、混み合っただけで会議中に鳴りうる。
        # 最後に取れた結果のほうが今に近い
        if self._stalled_sample is not None:
            if self._stalled_sample.is_alive():
                return self._last_activity
            self._stalled_sample = None
        try:
            self._last_activity = call_with_deadline(
                self._probe.sample, timeout=SAMPLE_TIMEOUT_SECONDS
            )
            return self._last_activity
        except Stalled as stalled:
            self._stalled_sample = stalled.thread
            _log.warning("音声の入出力を取得できません: %s", stalled)
            return self._last_activity
        except Exception as error:
            # 取れないときは鳴らす側に倒す。自動ミュートは補助で、黙り続けるほうが困る
            _log.warning("音声の入出力を取得できません: %s", error)
            return None

    async def wait_state(self) -> MuteState:
        """ほかのアプリの音が止むのを最大 audio_output_wait_seconds 待ってから判定する。

        通知音のような短い音の後には鳴らし、動画や音楽の再生中は黙るため。
        手動とマイクは待たずに返す。
        """
        deadline = self._monotonic() + self._config.audio_output_wait_seconds
        while True:
            # CoreAudio の取得（約13ms）が何度も続くので、イベントループを塞がない
            state = await asyncio.to_thread(self.state)
            if state.reason != "audio_output" or self._monotonic() >= deadline:
                return state
            await self._sleep(WAIT_POLL_SECONDS)


class PlaybackWatch:
    """鳴っている読み上げ1件の見張り。check() を周期的に呼ぶ。

    - 手動ミュートとマイクは、入った時点で止める
    - ほかのアプリの音は、まず音量を下げる（通知音ならそのままやり過ごす）。自動ミュートが
      有効で、audio_output_wait_seconds を超えて続いたら止める（鳴らす前の wait_state と同じ猶予）
    - bypass_mute（`pairvoice say`）は止めず、音量を下げるだけ
    """

    def __init__(self, controller: MuteController, *, bypass_mute: bool) -> None:
        self._controller = controller
        self._bypass_mute = bypass_mute
        self._output_since: float | None = None

    def check(self) -> tuple[str | None, bool]:
        """（止める理由、音量を下げるか）を返す。止めないなら理由は None。"""
        controller = self._controller
        config = controller._config
        if not self._bypass_mute and controller._manual_state() is not None:
            return "manual", False
        activity = controller._sample()
        if activity is None:
            return None, False
        if not self._bypass_mute and config.auto_microphone and activity.microphone:
            return "microphone", False
        if not activity.output:
            self._output_since = None
            return None, False

        now = controller._monotonic()
        if self._output_since is None:
            self._output_since = now
        sustained = now - self._output_since >= config.audio_output_wait_seconds
        if not self._bypass_mute and config.auto_audio_output and sustained:
            return "audio_output", True
        return None, True
