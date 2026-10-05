import asyncio
import contextlib
import time

import pytest

from pairvoice.lifecycle import (
    ManagedModel,
    ModelUnavailable,
    Runner,
    Superseded,
)
from tests.fakes import FakeBackend, gather_results


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_model(backend=None, *, idle=600.0, timeout=5.0):
    clock = FakeClock()
    model = ManagedModel(
        backend=backend or FakeBackend(),
        idle_unload_seconds=idle,
        load_timeout_seconds=timeout,
        clock=clock,
    )
    return model, clock


@pytest.mark.asyncio
async def test_ensure_loaded_loads_once():
    backend = FakeBackend()
    model, _ = make_model(backend)

    await model.ensure_loaded()
    await model.ensure_loaded()

    assert backend.load_calls == 1
    assert model.snapshot()["state"] == "loaded"


@pytest.mark.asyncio
async def test_concurrent_requests_join_a_single_load():
    backend = FakeBackend(load_delay=0.05)
    model, _ = make_model(backend)

    results = await gather_results([model.ensure_loaded() for _ in range(5)])

    assert [r for r in results if isinstance(r, Exception)] == []
    assert backend.load_calls == 1


@pytest.mark.asyncio
async def test_download_precedes_load_and_reports_downloading():
    backend = FakeBackend(downloaded=False, load_delay=0.05)
    model, _ = make_model(backend)

    task = asyncio.create_task(model.ensure_loaded())
    await asyncio.sleep(0.01)
    state_during = model.snapshot()["state"]
    await task

    assert state_during in {"downloading", "loading"}
    assert backend.download_calls == 1
    assert backend.load_calls == 1


@pytest.mark.asyncio
async def test_without_waiting_for_download_refuses_and_keeps_downloading():
    # フックは初回のダウンロードを待たない。断った後もダウンロードは背景で続き、
    # 済めば次の要求で載る
    backend = FakeBackend(downloaded=False, download_delay=0.05)
    model, _ = make_model(backend)

    with pytest.raises(ModelUnavailable) as refused:
        await model.ensure_loaded(wait_download=False)
    assert refused.value.code == "model_downloading"

    for _ in range(100):
        await asyncio.sleep(0.01)
        if model.snapshot()["state"] == "loaded":
            break
    assert model.snapshot()["state"] == "loaded"
    assert backend.download_calls == 1

    await model.ensure_loaded(wait_download=False)
    assert backend.load_calls == 1


@pytest.mark.asyncio
async def test_without_waiting_for_download_still_waits_for_load():
    # 待たないのはダウンロードだけ。手元にあるモデルの読み込みはこれまでどおり待つ
    backend = FakeBackend(load_delay=0.05)
    model, _ = make_model(backend)

    await model.ensure_loaded(wait_download=False)

    assert model.snapshot()["state"] == "loaded"


@pytest.mark.asyncio
async def test_load_failure_reports_and_retries():
    backend = FakeBackend(fail_load=True)
    model, _ = make_model(backend)

    with pytest.raises(ModelUnavailable) as first:
        await model.ensure_loaded()
    assert first.value.code == "model_load_failed"
    assert model.snapshot()["state"] == "failed"

    backend.fail_load = False
    await model.ensure_loaded()

    assert model.snapshot()["state"] == "loaded"
    assert backend.load_calls == 2


@pytest.mark.asyncio
async def test_load_timeout_reports_model_load_failed():
    # タイムアウトは実時間で待つので、FakeClock ではなく実クロックを使う。
    # ロードの実体は time.sleep(0.5) で、0.05 秒のタイムアウトを実際に起こす
    backend = FakeBackend(load_delay=0.5)
    model = ManagedModel(
        backend=backend,
        idle_unload_seconds=600.0,
        load_timeout_seconds=0.05,
        clock=time.monotonic,
    )

    with pytest.raises(ModelUnavailable) as error:
        await model.ensure_loaded()

    assert error.value.code == "model_load_failed"

    # 背景の _load タスクはこの時点でまだ走っている（残り約0.45秒）。
    # テストが即座に戻ると、イベントループが閉じられる際に
    # "Task was destroyed but it is pending!" 警告が出るため、
    # 完了するまで待ってから戻る（結果・例外は捨てる）。
    pending = model._loading
    if pending is not None:
        with contextlib.suppress(BaseException):
            await pending


@pytest.mark.asyncio
async def test_timeout_does_not_corrupt_state_while_load_continues():
    # タイムアウトで raise した直後も、背景の _load は
    # まだ走っているので snapshot() の state は "failed" と嘘をつかず
    # "loading" のままであること。
    backend = FakeBackend(load_delay=0.3)
    model = ManagedModel(
        backend=backend,
        idle_unload_seconds=600.0,
        load_timeout_seconds=0.05,
        clock=time.monotonic,
    )

    with pytest.raises(ModelUnavailable):
        await model.ensure_loaded()

    assert model.snapshot()["state"] == "loading"

    pending = model._loading
    if pending is not None:
        with contextlib.suppress(BaseException):
            await pending


@pytest.mark.asyncio
async def test_second_joiner_also_gets_timeout_on_shared_load():
    # 1人目がタイムアウトした直後に、同じ in-flight の
    # ロードへ2人目が合流しても、自分の load_timeout_seconds が効いて
    # timeout すること。state を FAILED に潰すと、2人目は背景ロードの完了まで
    # 無制限に待ってしまう。
    backend = FakeBackend(load_delay=0.3)
    model = ManagedModel(
        backend=backend,
        idle_unload_seconds=600.0,
        load_timeout_seconds=0.05,
        clock=time.monotonic,
    )

    with pytest.raises(ModelUnavailable):
        await model.ensure_loaded()

    with pytest.raises(ModelUnavailable) as second:
        await model.ensure_loaded()
    assert second.value.code == "model_load_failed"

    pending = model._loading
    if pending is not None:
        with contextlib.suppress(BaseException):
            await pending


@pytest.mark.asyncio
async def test_preflight_problem_marks_misconfigured():
    backend = FakeBackend(preflight_problem="profile_missing")
    model, _ = make_model(backend)

    with pytest.raises(ModelUnavailable) as error:
        await model.ensure_loaded()

    assert error.value.code == "profile_missing"
    assert model.snapshot()["state"] == "misconfigured"
    assert backend.load_calls == 0


@pytest.mark.asyncio
async def test_idle_unload_counts_from_load_when_never_used():
    backend = FakeBackend()
    model, clock = make_model(backend, idle=60.0)
    await model.ensure_loaded()

    clock.advance(59)
    assert await model.unload_if_idle() is False

    clock.advance(2)
    assert await model.unload_if_idle() is True
    assert backend.unload_calls == 1
    assert model.snapshot()["state"] == "unloaded"


@pytest.mark.asyncio
async def test_touch_postpones_idle_unload():
    model, clock = make_model(idle=60.0)
    await model.ensure_loaded()

    clock.advance(50)
    model.touch()
    clock.advance(50)

    assert await model.unload_if_idle() is False


@pytest.mark.asyncio
async def test_unload_if_idle_is_noop_when_not_loaded():
    backend = FakeBackend()
    model, clock = make_model(backend, idle=1.0)

    clock.advance(100)

    assert await model.unload_if_idle() is False
    assert backend.unload_calls == 0


@pytest.mark.asyncio
async def test_runner_serialises_calls():
    runner = Runner()
    active = 0
    peak = 0

    async def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return "done"

    results = await asyncio.gather(*[runner.run(work, droppable=False) for _ in range(4)])

    assert results == ["done"] * 4
    assert peak == 1


@pytest.mark.asyncio
async def test_waiting_droppable_call_is_superseded():
    runner = Runner()
    started = asyncio.Event()

    async def blocker():
        started.set()
        await asyncio.sleep(0.05)
        return "blocker"

    async def summary(tag):
        return tag

    blocking = asyncio.create_task(runner.run(blocker, droppable=False))
    await started.wait()

    first = asyncio.create_task(runner.run(lambda: summary("first"), droppable=True))
    await asyncio.sleep(0)
    second = asyncio.create_task(runner.run(lambda: summary("second"), droppable=True))

    results = await gather_results([blocking, first, second])

    assert results[0] == "blocker"
    assert isinstance(results[1], Superseded)
    assert results[2] == "second"
    assert runner.dropped_recent() == 1


@pytest.mark.asyncio
async def test_non_droppable_calls_are_never_superseded():
    runner = Runner()
    started = asyncio.Event()

    async def blocker():
        started.set()
        await asyncio.sleep(0.05)
        return "blocker"

    blocking = asyncio.create_task(runner.run(blocker, droppable=False))
    await started.wait()

    speeches = [
        asyncio.create_task(runner.run(lambda tag=tag: _echo(tag), droppable=False))
        for tag in ("a", "b", "c")
    ]
    results = await gather_results([blocking, *speeches])

    assert [r for r in results if isinstance(r, Exception)] == []
    assert runner.dropped_recent() == 0


async def _echo(value):
    return value


@pytest.mark.asyncio
async def test_dropped_recent_forgets_old_drops():
    clock = FakeClock()
    runner = Runner(clock=clock)
    started = asyncio.Event()

    async def blocker():
        started.set()
        await asyncio.sleep(0.05)

    blocking = asyncio.create_task(runner.run(blocker, droppable=False))
    await started.wait()
    first = asyncio.create_task(runner.run(_noop, droppable=True))
    await asyncio.sleep(0)
    second = asyncio.create_task(runner.run(_noop, droppable=True))
    await gather_results([blocking, first, second])

    assert runner.dropped_recent() == 1

    clock.advance(301)

    assert runner.dropped_recent() == 0


async def _noop():
    return None


@pytest.mark.asyncio
async def test_cancelled_waiting_droppable_call_does_not_leak_into_superseded():
    # 待機中の droppable な呼び出しが lock 取得前に
    # 外部キャンセルされると、自分で後片付けのコードパスに到達できない。
    # finally での後片付けが無いと、_superseded / _pending に自分の token が
    # 残り続け、誰にも回収されない残骸になる。
    runner = Runner()
    started = asyncio.Event()

    async def blocker():
        started.set()
        await asyncio.sleep(0.05)
        return "blocker"

    async def summary(tag):
        return tag

    blocking = asyncio.create_task(runner.run(blocker, droppable=False))
    await started.wait()

    first = asyncio.create_task(runner.run(lambda: summary("first"), droppable=True))
    await asyncio.sleep(0)  # first が _pending として登録され、lock 待ちに入るのを待つ

    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first

    assert runner._pending is None
    assert runner._superseded == set()

    second = asyncio.create_task(runner.run(lambda: summary("second"), droppable=True))

    results = await gather_results([blocking, second])

    assert results[0] == "blocker"
    assert results[1] == "second"
    assert runner.dropped_recent() == 0
    assert runner._superseded == set()
    assert runner._pending is None


@pytest.mark.asyncio
async def test_preflight_problem_while_loaded_keeps_state_and_still_unloads():
    # 載っている重みは preflight が崩れても残るので、状態を書き換えると解放されなくなる
    backend = FakeBackend()
    model, clock = make_model(backend, idle=60.0)
    await model.ensure_loaded()

    backend.preflight_problem = "profile_missing"
    with pytest.raises(ModelUnavailable) as error:
        await model.ensure_loaded()

    assert error.value.code == "profile_missing"
    assert model.snapshot()["state"] == "loaded"
    model.refresh_preflight()
    assert model.snapshot()["state"] == "loaded"

    clock.advance(61)
    assert await model.unload_if_idle() is True
    assert backend.unload_calls == 1
    model.refresh_preflight()
    assert model.snapshot()["state"] == "misconfigured"


@pytest.mark.asyncio
async def test_preflight_problem_while_loading_does_not_overwrite_state():
    backend = FakeBackend(load_delay=0.05)
    model, _ = make_model(backend)
    first = asyncio.create_task(model.ensure_loaded())
    await asyncio.sleep(0.01)

    backend.preflight_problem = "profile_missing"
    with pytest.raises(ModelUnavailable):
        await model.ensure_loaded()
    assert model.snapshot()["state"] == "loading"
    await first

    assert model.snapshot()["state"] == "loaded"
    assert backend.load_calls == 1


@pytest.mark.asyncio
async def test_preflight_problem_while_loaded_is_reported_in_detail():
    # 状態は重みに合わせて loaded のまま、/speak が 503 になる理由は detail で見せる
    backend = FakeBackend()
    model, _ = make_model(backend)
    await model.ensure_loaded()

    backend.preflight_problem = "profile_missing"
    with pytest.raises(ModelUnavailable):
        await model.ensure_loaded()
    assert model.snapshot()["detail"] == "profile_missing"

    backend.preflight_problem = None
    model.refresh_preflight()
    assert model.snapshot()["detail"] == ""

    # health() からも、ensure_loaded を通らずに気づける
    backend.preflight_problem = "profile_missing"
    model.refresh_preflight()
    assert (model.snapshot()["state"], model.snapshot()["detail"]) == ("loaded", "profile_missing")

    backend.preflight_problem = None
    await model.ensure_loaded()
    assert model.snapshot()["detail"] == ""
