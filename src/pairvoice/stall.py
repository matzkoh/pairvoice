"""CoreAudio のように、まれに戻らなくなる呼び出しを時間で見切る。

固まったスレッドは取り消せないので、使い捨ての daemon スレッドで呼ぶ。asyncio.to_thread の
共有プールで固まると、ほかの to_thread（ミュートの判定、ダウンロード）と取り合いになり、
終了時の join でサーバーの停止まで止まる。
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from typing import Any


class Stalled(Exception):
    """時間内に戻らなかった。thread はまだ呼び出しの中にいる。"""

    def __init__(self, thread: threading.Thread) -> None:
        super().__init__(f"{thread.name} が応答しません")
        self.thread = thread


def _spawn(fn: Callable[..., Any], args: tuple, deliver: Callable[[Any, Exception | None], None]):
    """fn(*args) を daemon スレッドで呼び、戻ったら deliver(result, error) を呼ぶ。"""

    def run() -> None:
        try:
            result, error = fn(*args), None
        except Exception as failed:
            result, error = None, failed
        deliver(result, error)

    thread = threading.Thread(target=run, name=getattr(fn, "__name__", "call"), daemon=True)
    thread.start()
    return thread


def call_with_deadline(fn: Callable[..., Any], *args, timeout: float) -> Any:
    """fn を daemon スレッドで呼び、timeout 秒待っても戻らなければ Stalled を投げる。

    呼んだスレッドは最大 timeout 秒止まる。ふだん数十ミリ秒で戻る呼び出しに使う。
    """
    outcome: list = []
    thread = _spawn(fn, args, lambda result, error: outcome.append((result, error)))
    thread.join(timeout)
    if not outcome:
        raise Stalled(thread)
    result, error = outcome[0]
    if error is not None:
        raise error
    return result


def start_in_daemon_thread(fn: Callable[..., Any], *args) -> asyncio.Future:
    """fn を daemon スレッドで呼び、結果をイベントループの Future で返す。

    待つ側は asyncio.wait の timeout で見切る。見切った後に戻ってきた結果は Future に入るので、
    後始末は done callback で行う。
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def settle(result, error) -> None:
        if future.cancelled():
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def deliver(result, error) -> None:
        with contextlib.suppress(RuntimeError):  # 戻る前にイベントループが閉じていた
            loop.call_soon_threadsafe(settle, result, error)

    _spawn(fn, args, deliver)
    return future
