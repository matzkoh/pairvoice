import asyncio
import threading

import pytest

from pairvoice.stall import Stalled, call_with_deadline, start_in_daemon_thread


def test_returns_the_result_and_raises_the_error():
    assert call_with_deadline(lambda: 42, timeout=1) == 42
    with pytest.raises(ValueError, match="invalid literal"):
        call_with_deadline(lambda: int("x"), timeout=1)


def test_gives_up_on_a_call_that_does_not_return():
    release = threading.Event()
    try:
        with pytest.raises(Stalled) as stalled:
            call_with_deadline(release.wait, 5, timeout=0.05)
        assert stalled.value.thread.is_alive()
    finally:
        release.set()


async def test_delivers_a_late_result_to_the_future():
    release = threading.Event()
    future = start_in_daemon_thread(lambda: release.wait(5) and "late")

    done, _ = await asyncio.wait({future}, timeout=0.05)
    assert not done

    release.set()
    assert await asyncio.wait_for(future, 1) == "late"
