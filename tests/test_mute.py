import threading

import pytest

from pairvoice import mute as mute_module
from pairvoice.config import MuteConfig
from pairvoice.mute import WAIT_POLL_SECONDS, InvalidMinutes, MuteController
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


def test_not_muted_by_default():
    controller, _ = make_controller()

    state = controller.state()

    assert state.active is False
    assert state.reason is None
    assert state.until is None


def test_manual_mute_expires():
    controller, clock = make_controller()

    state = controller.mute(30)
    assert state.active is True
    assert state.reason == "manual"
    assert state.until is not None

    clock.advance(29 * 60)
    assert controller.state().active is True

    clock.advance(2 * 60)
    assert controller.state().active is False


def test_unmute_clears_manual_mute():
    controller, _ = make_controller()
    controller.mute(30)

    assert controller.unmute().active is False
    assert controller.state().active is False


@pytest.mark.parametrize("minutes", [0, -1, 481, None, 1.5, "30"])
def test_invalid_minutes_rejected(minutes):
    controller, _ = make_controller()

    with pytest.raises(InvalidMinutes):
        controller.mute(minutes)


@pytest.mark.parametrize("minutes", [1, 480])
def test_boundary_minutes_accepted(minutes):
    controller, _ = make_controller()

    assert controller.mute(minutes).active is True


def test_microphone_mutes_when_enabled():
    controller, _ = make_controller(probe=FakeProbe(microphone=True))

    state = controller.state()

    assert state.active is True
    assert state.reason == "microphone"
    assert state.until is None


def test_microphone_ignored_when_disabled():
    controller, _ = make_controller(
        config=MuteConfig(auto_microphone=False),
        probe=FakeProbe(microphone=True),
    )

    assert controller.state().active is False


def test_audio_output_mutes_while_playing():
    controller, _ = make_controller(probe=FakeProbe(output=True))

    state = controller.state()

    assert state.active is True
    assert state.reason == "audio_output"


def test_audio_output_can_be_disabled():
    config = MuteConfig(auto_audio_output=False)
    controller, _ = make_controller(config=config, probe=FakeProbe(output=True))

    assert controller.state().active is False


def test_audio_output_can_be_left_out_of_a_check():
    controller, _ = make_controller(probe=FakeProbe(output=True))

    assert controller.state(include_output=False).active is False


def test_watch_ducks_under_a_short_sound():
    controller, _ = make_controller(probe=FakeProbe(output=True))

    assert controller.watch().check() == (None, True)


def test_watch_stops_when_other_audio_keeps_playing():
    controller, clock = make_controller(probe=FakeProbe(output=True))
    watch = controller.watch()

    watch.check()
    clock.advance(5)

    assert watch.check() == ("audio_output", True)


def test_watch_restarts_the_grace_period_after_silence():
    probe = FakeProbe(output=True)
    controller, clock = make_controller(probe=probe)
    watch = controller.watch()

    watch.check()
    clock.advance(4)
    probe.output = False
    watch.check()
    probe.output = True
    clock.advance(4)

    assert watch.check() == (None, True)


def test_watch_only_ducks_when_audio_output_auto_mute_is_off():
    config = MuteConfig(auto_audio_output=False)
    controller, clock = make_controller(config=config, probe=FakeProbe(output=True))
    watch = controller.watch()

    watch.check()
    clock.advance(60)

    assert watch.check() == (None, True)


@pytest.mark.parametrize(
    ("manual", "microphone", "reason"), [(True, False, "manual"), (False, True, "microphone")]
)
def test_watch_stops_for_manual_and_microphone(manual, microphone, reason):
    controller, _ = make_controller(probe=FakeProbe(microphone=microphone))
    if manual:
        controller.mute(15)

    assert controller.watch().check() == (reason, False)


def test_watch_with_bypass_never_stops():
    controller, clock = make_controller(probe=FakeProbe(microphone=True, output=True))
    controller.mute(15)
    watch = controller.watch(bypass_mute=True)

    watch.check()
    clock.advance(60)

    assert watch.check() == (None, True)


def test_manual_reason_wins_over_automatic():
    controller, _ = make_controller(probe=FakeProbe(microphone=True, output=True))
    controller.mute(30)

    assert controller.state().reason == "manual"


def test_microphone_reason_wins_over_audio_output():
    controller, _ = make_controller(probe=FakeProbe(microphone=True, output=True))

    assert controller.state().reason == "microphone"


def test_probe_is_not_touched_when_auto_mute_is_off():
    probe = FakeProbe(microphone=True, output=True)
    config = MuteConfig(auto_microphone=False, auto_audio_output=False)
    controller, _ = make_controller(config=config, probe=probe)

    assert controller.state().active is False
    assert probe.samples == 0


def test_probe_failure_does_not_mute():
    controller, _ = make_controller(probe=FakeProbe(error=RuntimeError("CoreAudio")))

    assert controller.state().active is False


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


def test_wall_clock_jumps_do_not_stretch_the_sustained_output_timer():
    # 時計合わせ（NTP・手動）で壁時計が戻っても、鳴り続けた時間は単調時計で数える
    wall = FakeClock()
    steady = FakeClock()
    controller = MuteController(
        config=MuteConfig(), probe=FakeProbe(output=True), clock=wall, monotonic=steady
    )
    watch = controller.watch()

    assert watch.check() == (None, True)
    wall.advance(-3600)
    steady.advance(6)

    assert watch.check() == ("audio_output", True)


def test_a_hung_sample_counts_as_unknown_and_is_not_called_again(monkeypatch):
    # CoreAudio が詰まると取得が戻らない。state() は /health からも呼ぶので見切り、
    # 戻るまでは次を呼ばない（固まったスレッドを積み上げない）
    monkeypatch.setattr(mute_module, "SAMPLE_TIMEOUT_SECONDS", 0.05)

    class HungProbe(FakeProbe):
        def __init__(self):
            super().__init__(microphone=True)
            self.device = threading.Event()

        def sample(self):
            self.device.wait(5)
            return super().sample()

    probe = HungProbe()
    controller, _ = make_controller(probe=probe)
    try:
        assert controller.state().active is False
        assert controller.state().active is False
        assert probe.samples == 0
    finally:
        probe.device.set()

    controller._stalled_sample.join(1)
    assert controller.state().reason == "microphone"
