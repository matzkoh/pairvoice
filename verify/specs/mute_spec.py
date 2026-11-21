"""ミュート判定（MuteController と PlaybackWatch）の性質。

設定・取得結果・経過時間を任意に選ばせ、判定がすべての組み合わせで決まりどおりになることを確かめる。
時間は整数秒で与える。壁時計の値（約 1.8e9）に小数秒を足すと 0.2 マイクロ秒の単位で丸まり、
期限ちょうどの判定が丸めの向きで揺れるが、それは守りたい決まりではない。
"""

import logging
from collections.abc import Callable
from typing import Any

from pairvoice import mute
from pairvoice.config import MuteConfig
from pairvoice.mute import MAX_MINUTES, MIN_MINUTES, InvalidMinutes, MuteController
from tests.fakes import FakeProbe


def _call_directly(fn: Callable[..., Any], *args, timeout: float) -> Any:
    """取得を見切る daemon スレッドは記号実行の外になるので、ここでは直接呼ぶ。

    見切ったときの振る舞いは tests/test_mute.py と tests/test_stall.py が確かめる。
    """
    return fn(*args)


mute.call_with_deadline = _call_directly  # ty: ignore[invalid-assignment]
# 取得の失敗を書くログが時刻を読み、記号のまま float にできず落ちる
logging.disable()


class _Instant:
    """datetime.fromtimestamp の代役。期限を見せる形に直すだけで判定には使われないが、
    本物は記号のままの時刻を具体的な値ごとに展開し、経路が数えきれなくなる。"""

    def __init__(self, timestamp: float) -> None:
        self.timestamp = timestamp

    @classmethod
    def fromtimestamp(cls, timestamp: float) -> "_Instant":
        return cls(timestamp)

    def astimezone(self) -> "_Instant":
        return self


mute.datetime = _Instant  # ty: ignore[invalid-assignment]

START = 1_800_000_000


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


def _controller(
    probe: FakeProbe,
    auto_microphone: bool = True,
    auto_audio_output: bool = True,
    wait: float = 5.0,
) -> tuple[MuteController, Clock, Clock]:
    wall, steady = Clock(), Clock()
    config = MuteConfig(
        auto_microphone=auto_microphone,
        auto_audio_output=auto_audio_output,
        audio_output_wait_seconds=wait,
    )
    return MuteController(config, probe, clock=wall, monotonic=steady), wall, steady


def state_follows_reason_priority(
    manual: bool,
    auto_microphone: bool,
    auto_audio_output: bool,
    include_output: bool,
    microphone: bool,
    output: bool,
    fails: bool,
) -> bool:
    """手動 > マイク > 音声の再生、の順で理由を選ぶ。

    取得に失敗したら鳴らす側に倒す。手動ミュート中と、見る自動条件が無いときは取得しない。

    post: __return__
    """
    probe = FakeProbe(microphone, output, RuntimeError("CoreAudio") if fails else None)
    controller, _, _ = _controller(probe, auto_microphone, auto_audio_output)
    if manual:
        controller.mute(30)
    probe.samples = 0
    state = controller.state(include_output=include_output)

    check_output = include_output and auto_audio_output
    looks = not manual and (auto_microphone or check_output)
    if manual:
        reason = "manual"
    elif looks and not fails and auto_microphone and microphone:
        reason = "microphone"
    elif looks and not fails and check_output and output:
        reason = "audio_output"
    else:
        reason = None
    return (
        state.reason == reason
        and state.active == (reason is not None)
        and (state.until is not None) == manual
        and probe.samples == (1 if looks else 0)
    )


def mute_refuses_anything_but_an_int(minutes: float | str | bool | None) -> bool:
    """明示ミュートは整数分だけ受け付ける（小数、文字列、真偽値、無期限は断る）。

    post: __return__
    """
    controller, _, _ = _controller(FakeProbe())
    try:
        controller.mute(minutes)
    except InvalidMinutes:
        return True
    return False


def mute_accepts_only_minutes_in_range(minutes: int) -> bool:
    """整数分は 1〜480 だけ受け付ける。

    post: __return__
    """
    controller, _, _ = _controller(FakeProbe())
    valid = MIN_MINUTES <= minutes <= MAX_MINUTES
    try:
        state = controller.mute(minutes)
    except InvalidMinutes:
        return not valid
    return valid and state.reason == "manual"


def manual_mute_lasts_until_it_expires_or_is_cleared(
    minutes: int, elapsed: int, unmute: bool
) -> bool:
    """手動ミュートは期限の直前まで続き、期限を過ぎるか解除すると切れる。

    pre: MIN_MINUTES <= minutes <= MAX_MINUTES
    pre: 0 <= elapsed <= 2 * MAX_MINUTES * 60
    post: __return__
    """
    controller, wall, _ = _controller(FakeProbe(), auto_microphone=False, auto_audio_output=False)
    controller.mute(minutes)
    if unmute:
        controller.unmute()
    wall.now += elapsed
    return controller.state().active == (not unmute and elapsed < minutes * 60)


def _expected_watch(
    steps: list[tuple[bool, bool, int]],
    *,
    manual: bool,
    bypass: bool,
    auto_microphone: bool,
    auto_audio_output: bool,
    wait: int,
) -> list[tuple[str | None, bool]]:
    """PlaybackWatch.check() が各回に返すはずの（止める理由、音量を下げるか）。"""
    results = []
    now = 0
    output_since = None
    for microphone, output, elapsed in steps:
        now += elapsed
        if not bypass and manual:
            results.append(("manual", False))
        elif not bypass and auto_microphone and microphone:
            results.append(("microphone", False))
        elif not output:
            output_since = None
            results.append((None, False))
        else:
            output_since = now if output_since is None else output_since
            sustained = now - output_since >= wait
            stop = not bypass and auto_audio_output and sustained
            results.append(("audio_output" if stop else None, True))
    return results


def _run_watch(
    steps: list[tuple[bool, bool, int]],
    *,
    manual: bool,
    bypass: bool,
    auto_microphone: bool,
    auto_audio_output: bool,
    wait: int,
    wall_jump: int = 0,
) -> bool:
    """見張りを steps のとおりに呼び、各回の判定が _expected_watch と一致するか。"""
    probe = FakeProbe()
    controller, wall, steady = _controller(probe, auto_microphone, auto_audio_output, wait)
    if manual:
        controller.mute(MAX_MINUTES)
    watch = controller.watch(bypass_mute=bypass)
    actual = []
    for microphone, output, elapsed in steps:
        probe.microphone, probe.output = microphone, output
        steady.now += elapsed
        wall.now += wall_jump
        actual.append(watch.check())
    expected = _expected_watch(
        steps,
        manual=manual,
        bypass=bypass,
        auto_microphone=auto_microphone,
        auto_audio_output=auto_audio_output,
        wait=wait,
    )
    return actual == expected


def watch_stops_at_once_for_manual_and_microphone(
    manual: bool,
    bypass: bool,
    auto_microphone: bool,
    auto_audio_output: bool,
    microphone: bool,
    output: bool,
) -> bool:
    """手動ミュートとマイクは、鳴り始めの1回目の見張りで止める（音量は下げない）。

    bypass は止めず、ほかのアプリの音があれば下げるだけ。

    post: __return__
    """
    return _run_watch(
        [(microphone, output, 0)],
        manual=manual,
        bypass=bypass,
        auto_microphone=auto_microphone,
        auto_audio_output=auto_audio_output,
        wait=5,
    )


def watch_stops_only_after_output_keeps_playing(
    bypass: bool,
    auto_microphone: bool,
    auto_audio_output: bool,
    wait: int,
    m1: bool,
    o1: bool,
    m2: bool,
    o2: bool,
    e2: int,
    m3: bool,
    o3: bool,
    e3: int,
    wall_jump: int,
) -> bool:
    """見張りを3回続けて呼んだときの判定。

    ほかのアプリの音はまず音量を下げ、自動ミュートが有効で wait 秒を超えて鳴り続けたら止める
    （途中で音が止んだら数え直す）。bypass は止めず、下げるだけ。鳴り続けた時間は単調時計で
    数え、壁時計がどちらへ飛んでも変わらない。

    pre: 0 <= wait <= 60 and 0 <= e2 <= 60 and 0 <= e3 <= 60
    pre: -3600 <= wall_jump <= 3600
    post: __return__
    """
    return _run_watch(
        [(m1, o1, 0), (m2, o2, e2), (m3, o3, e3)],
        manual=False,
        bypass=bypass,
        auto_microphone=auto_microphone,
        auto_audio_output=auto_audio_output,
        wait=wait,
        wall_jump=wall_jump,
    )
