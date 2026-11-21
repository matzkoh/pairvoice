import threading

import pytest

from pairvoice import mute as mute_module
from pairvoice.config import MuteConfig
from pairvoice.mute import WAIT_POLL_SECONDS, MuteController
from tests.fakes import FakeProbe


class FakeClock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_controller(config=None, probe=None, on_sleep=None):
    clock = FakeClock()

    async def sleep(seconds):
        clock.advance(seconds)
        if on_sleep is not None:
            on_sleep(clock)

    controller = MuteController(
        config=config or MuteConfig(),
        probe=probe or FakeProbe(),
        clock=clock,
        monotonic=clock,
        sleep=sleep,
    )
    return controller, clock


async def test_wait_state_speaks_after_a_short_sound():
    probe = FakeProbe(output=True)
    started = 1_800_000_000.0

    def stop_after_two_seconds(clock):
        if clock.now - started >= 2:
            probe.output = False

    controller, clock = make_controller(probe=probe, on_sleep=stop_after_two_seconds)

    state = await controller.wait_state()

    assert state.active is False
    assert clock.now - started == pytest.approx(2.0)


async def test_wait_state_gives_up_while_still_playing():
    config = MuteConfig(audio_output_wait_seconds=5)
    controller, clock = make_controller(config=config, probe=FakeProbe(output=True))
    started = clock.now

    state = await controller.wait_state()

    assert state.reason == "audio_output"
    assert 5 <= clock.now - started < 5 + WAIT_POLL_SECONDS


@pytest.mark.parametrize(
    ("probe", "manual", "reason"),
    [
        (FakeProbe(microphone=True, output=True), False, "microphone"),
        (FakeProbe(output=True), True, "manual"),
        (FakeProbe(), False, None),
    ],
)
async def test_wait_state_returns_at_once_unless_audio_output(probe, manual, reason):
    controller, clock = make_controller(probe=probe)
    if manual:
        controller.mute(30)
    started = clock.now

    state = await controller.wait_state()

    assert state.reason == reason
    assert clock.now == started


def test_a_hung_sample_uses_the_last_result_and_is_not_called_again(monkeypatch):
    # CoreAudio が詰まると取得が戻らない。state() は /health からも呼ぶので見切り、
    # 最後に取れた結果を使う。戻るまでは次を呼ばない（固まったスレッドを積み上げない）
    monkeypatch.setattr(mute_module, "SAMPLE_TIMEOUT_SECONDS", 0.05)

    class HungProbe(FakeProbe):
        def __init__(self):
            super().__init__(microphone=True)
            self.device = threading.Event()

        def sample(self):
            if self.samples:
                self.device.wait(5)
            return super().sample()

    probe = HungProbe()
    controller, _ = make_controller(probe=probe)
    try:
        assert controller.state().reason == "microphone"  # 1回目は取れる
        probe.microphone = False
        assert controller.state().reason == "microphone"  # 詰まったので最後の結果
        assert controller.state().reason == "microphone"  # 詰まっている間は呼ばない
        assert probe.samples == 1
    finally:
        probe.device.set()

    controller._stalled_sample.join(1)
    assert controller.state().active is False
