import asyncio
import threading
from pathlib import Path

from pairvoice import player as player_module
from pairvoice.config import MuteConfig, PlaybackConfig
from pairvoice.mute import MuteController
from pairvoice.player import WATCH_INTERVAL_SECONDS, Player
from tests.fakes import FakeProbe


class FakeSound:
    def __init__(self, path, volume, ticks):
        self.path = path
        self.volumes = [volume]
        self.ticks = ticks
        self.stopped = False

    def playing(self):
        return not self.stopped and self.ticks > 0

    def set_volume(self, volume, fade_seconds):
        self.volumes.append(volume)

    def stop(self):
        self.stopped = True


class Rig:
    """Player と、時計・音・ミュートの偽物一式。on_tick(n) は見張りの n 回目の後に呼ばれる。"""

    def __init__(self, *, ticks=3, mute_config=None, playback=None, on_tick=None, before_open=None):
        self.now = 0.0
        self.probe = FakeProbe()
        self.mute = MuteController(
            mute_config or MuteConfig(),
            self.probe,
            clock=lambda: self.now,
            monotonic=lambda: self.now,
        )
        self.sounds: list[FakeSound] = []
        self.watched = 0
        self.on_tick = on_tick

        def opener(path, volume):
            if before_open is not None:
                before_open()
            sound = FakeSound(path, volume, ticks)
            self.sounds.append(sound)
            return sound

        async def sleep(seconds):
            self.now += seconds
            if seconds == WATCH_INTERVAL_SECONDS and self.sounds:
                self.watched += 1
                self.sounds[-1].ticks -= 1
                if self.on_tick is not None:
                    self.on_tick(self, self.watched)
            await asyncio.sleep(0)

        self.player = Player(
            playback or PlaybackConfig(volume=0.8, duck_volume=0.2),
            self.mute,
            opener=opener,
            clock=lambda: self.now,
            sleep=sleep,
        )

    def enqueue(self, name, **kwargs):
        self.player.enqueue(Path(name), **kwargs)

    def idle(self, expected_sounds):
        state = self.player.describe()
        return len(self.sounds) >= expected_sounds and not state["playing"] and not state["waiting"]

    async def drain(self, expected_sounds):
        task = asyncio.create_task(self.player.run())
        try:
            await wait_until(lambda: self.idle(expected_sounds))
        finally:
            task.cancel()


async def wait_until(condition, attempts=500):
    for _ in range(attempts):
        if condition():
            return
        await asyncio.sleep(0.002)


async def test_plays_queued_audio_in_order():
    rig = Rig()
    rig.enqueue("a.wav")
    rig.enqueue("b.wav")

    await rig.drain(2)

    assert [sound.path.name for sound in rig.sounds] == ["a.wav", "b.wav"]
    assert rig.sounds[0].volumes == [0.8]


async def test_skips_while_muted():
    rig = Rig()
    rig.mute.mute(15)
    rig.enqueue("a.wav")

    await rig.drain(0)

    assert rig.sounds == []


async def test_bypass_mute_plays_while_muted():
    rig = Rig()
    rig.mute.mute(15)
    rig.enqueue("a.wav", bypass_mute=True)

    await rig.drain(1)

    assert len(rig.sounds) == 1
    assert rig.sounds[0].stopped is False


async def test_stops_with_fade_when_microphone_turns_on():
    def on_tick(rig, n):
        rig.probe.microphone = n >= 1

    rig = Rig(ticks=10, on_tick=on_tick)
    rig.enqueue("a.wav")

    await rig.drain(1)

    sound = rig.sounds[0]
    assert sound.stopped is True
    assert sound.volumes[-1] == 0.0
    assert sound.ticks > 0


async def test_ducks_under_a_short_sound_and_restores():
    def on_tick(rig, n):
        rig.probe.output = n in (1, 2)

    rig = Rig(ticks=6, on_tick=on_tick)
    rig.enqueue("a.wav")

    await rig.drain(1)

    sound = rig.sounds[0]
    assert sound.stopped is False
    assert sound.volumes == [0.8, 0.2, 0.8]


async def test_stops_when_other_audio_keeps_playing():
    def on_tick(rig, n):
        rig.probe.output = n >= 1

    rig = Rig(ticks=100, mute_config=MuteConfig(audio_output_wait_seconds=1.0), on_tick=on_tick)
    rig.enqueue("a.wav")

    await rig.drain(1)

    sound = rig.sounds[0]
    assert sound.stopped is True
    assert sound.volumes == [0.8, 0.2, 0.0]


async def test_only_ducks_when_audio_output_auto_mute_is_off():
    def on_tick(rig, n):
        rig.probe.output = n >= 1

    config = MuteConfig(auto_audio_output=False, audio_output_wait_seconds=0.5)
    rig = Rig(ticks=10, mute_config=config, on_tick=on_tick)
    rig.enqueue("a.wav")

    await rig.drain(1)

    sound = rig.sounds[0]
    assert sound.stopped is False
    assert sound.volumes == [0.8, 0.2]


async def test_stop_cuts_current_and_drops_waiting():
    def on_tick(rig, n):
        if n == 1:
            assert rig.player.stop() == 2

    rig = Rig(ticks=10, on_tick=on_tick)
    rig.enqueue("a.wav")
    rig.enqueue("b.wav")

    await rig.drain(1)

    assert [sound.path.name for sound in rig.sounds] == ["a.wav"]
    assert rig.sounds[0].stopped is True


async def test_stop_drops_audio_still_being_synthesized():
    rig = Rig()
    accepted = rig.player.epoch
    rig.player.stop()
    rig.player.enqueue(Path("a.wav"), epoch=accepted)

    await rig.drain(0)

    assert rig.sounds == []


async def test_drops_audio_that_waited_too_long():
    rig = Rig(playback=PlaybackConfig(max_wait_seconds=60))
    rig.enqueue("a.wav")
    rig.now += 61

    await rig.drain(0)

    assert rig.sounds == []


async def test_gives_up_on_a_hung_audio_device_and_stops_the_late_sound(monkeypatch):
    # CoreAudio が詰まると AVAudioPlayer.play() が戻らない。見切って次へ進み、
    # 後から鳴り始めた音は見張れないので止める
    monkeypatch.setattr(player_module, "OPEN_TIMEOUT_SECONDS", 0.05)
    device = threading.Event()
    rig = Rig(before_open=lambda: device.wait(5))
    rig.enqueue("hung.wav")

    task = asyncio.create_task(rig.player.run())
    try:
        await asyncio.sleep(0.1)  # 見切るまで待つ
        assert rig.sounds == []  # まだ戻ってきていない
        assert rig.player.describe() == {"playing": False, "waiting": 0}

        device.set()
        await wait_until(lambda: rig.sounds and rig.sounds[0].stopped)
        assert rig.sounds[0].stopped

        rig.enqueue("next.wav")
        await wait_until(lambda: rig.idle(2))
        assert [sound.path.name for sound in rig.sounds] == ["hung.wav", "next.wav"]
    finally:
        device.set()
        task.cancel()
