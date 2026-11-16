import asyncio
import logging
import threading
from pathlib import Path

import numpy as np

from pairvoice import player as player_module
from pairvoice.config import MuteConfig, PlaybackConfig
from pairvoice.mute import MuteController
from pairvoice.player import WATCH_INTERVAL_SECONDS, Player
from tests.fakes import FakeProbe, InstantOutput, wait_until


class FakeSound:
    def __init__(self, path, volume, ticks):
        self.path = path
        self.volumes = [volume]
        self.ticks = ticks
        self.stopped = False

    def playing(self):
        return not self.stopped and self.ticks > 0

    def remaining(self):
        return self.ticks * WATCH_INTERVAL_SECONDS

    def set_volume(self, volume, fade_seconds):
        self.volumes.append(volume)

    def stop(self):
        self.stopped = True


class FakeStreamSound(FakeSound):
    """足された波形を覚える音。finish された後、残りの ticks を鳴らし切ると鳴り終わる。"""

    def __init__(self, sample_rate, volume, ticks):
        super().__init__(None, volume, ticks)
        self.sample_rate = sample_rate
        self.fed = []
        self.finished = False
        # 鳴っている最中に止められたか（鳴り終わった後の片付けとは区別する）
        self.interrupted = False

    def feed(self, pcm):
        self.fed.append(pcm)

    def finish(self):
        self.finished = True

    def playing(self):
        return not self.stopped and (not self.finished or self.ticks > 0)

    def stop(self):
        self.interrupted = self.interrupted or self.playing()
        self.stopped = True


class Rig:
    """Player と、時計・音・ミュートの偽物一式。on_tick(n) は見張りの n 回目の後に呼ばれる。"""

    def __init__(
        self,
        *,
        ticks=3,
        mute_config=None,
        playback=None,
        on_tick=None,
        before_open=None,
        prepare=None,
        shape=None,
        stream_open_error=None,
    ):
        self.now = 0.0
        self.probe = FakeProbe()
        self.mute = MuteController(
            mute_config or MuteConfig(),
            self.probe,
            clock=lambda: self.now,
            monotonic=lambda: self.now,
        )
        self.sounds: list[FakeSound] = []
        # sounds のうち、合成しながら鳴らした音
        self.streams: list[FakeStreamSound] = []
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

        def stream_opener(sample_rate, volume):
            if before_open is not None:
                before_open()
            if stream_open_error is not None:
                raise stream_open_error
            sound = FakeStreamSound(sample_rate, volume, ticks)
            self.sounds.append(sound)
            self.streams.append(sound)
            return sound

        self.player = Player(
            playback or PlaybackConfig(volume=0.8, duck_volume=0.2),
            self.mute,
            opener=opener,
            clock=lambda: self.now,
            sleep=sleep,
            prepare=prepare,
            stream_opener=stream_opener,
            shape=shape,
        )

    def enqueue(self, name, **kwargs):
        self.player.enqueue(Path(name), **kwargs)

    def open_stream(self, **kwargs):
        return self.player.open_stream(48000, **kwargs)

    def idle(self, expected_sounds):
        # 音は opener のスレッドで作られ、_play が playing を立てるのはその後なので、音の側も見る
        state = self.player.describe()
        return (
            len(self.sounds) >= expected_sounds
            and not any(sound.playing() for sound in self.sounds)
            and not state["playing"]
            and not state["waiting"]
        )

    async def drain(self, expected_sounds):
        task = asyncio.create_task(self.player.run())
        try:
            await wait_until(lambda: self.idle(expected_sounds))
        finally:
            task.cancel()


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


def test_a_hung_sound_counts_as_finished_and_is_left_alone(monkeypatch):
    # 鳴っている最中に CoreAudio が詰まっても、見張りのループをイベントループで固めない
    monkeypatch.setattr(player_module, "SOUND_CALL_TIMEOUT_SECONDS", 0.05)
    device = threading.Event()

    class HungPlayer:
        stops = 0

        def isPlaying(self):  # AVAudioPlayer の名前
            device.wait(5)
            return True

        def stop(self):
            HungPlayer.stops += 1

    sound = player_module._AVSound(HungPlayer())
    try:
        assert sound.playing() is False
        sound.stop()  # 固まったスレッドが中にいる間は触らない
        assert HungPlayer.stops == 0
    finally:
        device.set()


async def test_waits_only_until_the_sound_ends():
    # 文ごとの音を続けて鳴らすので、見張りの間隔ぶん文の間を空けない
    sleeps = []

    class EndingSound(FakeSound):
        def remaining(self):
            return 0.1 if self.ticks > 0 else 0.0

    async def sleep(seconds):
        sleeps.append(seconds)
        sound.ticks -= 1
        await asyncio.sleep(0)

    sound = EndingSound(Path("a.wav"), 0.8, ticks=1)
    player = Player(
        PlaybackConfig(volume=0.8),
        MuteController(MuteConfig(), FakeProbe()),
        opener=lambda path, volume: sound,
        sleep=sleep,
    )
    player.enqueue(Path("a.wav"))
    task = asyncio.create_task(player.run())
    try:
        await wait_until(lambda: sleeps and not player.describe()["playing"])
    finally:
        task.cancel()

    assert sleeps == [0.1]


async def test_plays_the_prepared_file_and_removes_it_afterwards(tmp_path):
    prepared = tmp_path / "a.spatial.wav"

    def prepare(path):
        prepared.write_bytes(b"")
        return prepared

    rig = Rig(prepare=prepare)
    rig.enqueue("a.wav")

    await rig.drain(1)

    assert rig.sounds[0].path == prepared
    assert not prepared.exists()


async def test_plays_the_original_when_preparing_fails_or_is_skipped():
    def prepare(path):
        if path.name == "a.wav":
            raise ValueError("broken")

    rig = Rig(prepare=prepare)
    rig.enqueue("a.wav")
    rig.enqueue("b.wav")

    await rig.drain(2)

    assert [sound.path.name for sound in rig.sounds] == ["a.wav", "b.wav"]


# --- 合成しながら鳴らす ---


def wave(value, frames=4800):
    return np.full(frames, value, np.float32)


async def running(rig):
    """Player を動かしておく。呼び手が止める。"""
    return asyncio.create_task(rig.player.run())


async def test_stream_plays_waves_as_they_arrive():
    rig = Rig()
    stream = rig.open_stream()
    task = await running(rig)
    try:
        stream.feed(wave(0.1))
        await wait_until(lambda: rig.streams and len(rig.streams[0].fed) == 1)
        # 合成はまだ続いている。届いた分だけ鳴らして、次を待つ
        assert rig.streams[0].finished is False
        stream.feed(wave(0.2))
        stream.close(Path("a.wav"))
        await wait_until(lambda: rig.idle(1))
    finally:
        task.cancel()

    sound = rig.streams[0]
    assert sound.sample_rate == 48000
    assert [pcm.shape for pcm in sound.fed] == [(4800, 2), (4800, 2)]
    assert [float(pcm[0, 0]) for pcm in sound.fed] == [np.float32(0.1), np.float32(0.2)]
    assert sound.finished is True
    assert sound.interrupted is False
    # 鳴り終わったら出力を手放す
    assert sound.stopped is True


async def test_stream_checks_mute_only_when_the_first_wave_arrives():
    rig = Rig()
    stream = rig.open_stream()
    task = await running(rig)
    try:
        # 出力の用意は先に済ませるが、鳴らすかどうかは音が届くまで決めない
        await wait_until(lambda: len(rig.streams) == 1)
        assert rig.probe.samples == 0
        rig.mute.mute(15)
        stream.feed(wave(0.1))
        stream.close()
        await wait_until(lambda: rig.streams[0].stopped)
    finally:
        task.cancel()

    assert rig.streams[0].fed == []
    assert rig.streams[0].interrupted is True


async def test_stream_that_ends_without_a_wave_plays_nothing():
    rig = Rig()
    stream = rig.open_stream()
    task = await running(rig)
    try:
        stream.abort()
        await wait_until(lambda: rig.streams and rig.streams[0].stopped)
    finally:
        task.cancel()

    assert rig.streams[0].fed == []
    assert rig.player.describe() == {"playing": False, "waiting": 0}


async def test_stop_drops_a_stream_still_waiting_for_its_first_wave():
    rig = Rig()
    stream = rig.open_stream()
    task = await running(rig)
    try:
        await wait_until(lambda: len(rig.streams) == 1)
        rig.player.stop()
        stream.feed(wave(0.1))
        stream.close()
        await wait_until(lambda: rig.streams[0].stopped)
    finally:
        task.cancel()

    assert rig.streams[0].fed == []


async def test_stop_cuts_a_playing_stream_and_drops_later_waves():
    # 偽の時計では見張りが一瞬で回るので、「止める」が届くまで鳴り終わらない長さにする
    rig = Rig(ticks=10**9)
    stream = rig.open_stream()
    task = await running(rig)
    try:
        stream.feed(wave(0.1))
        await wait_until(lambda: rig.streams and rig.streams[0].fed)
        assert rig.player.stop() == 1
        await wait_until(lambda: rig.streams[0].stopped)
        # 止めた後に合成の側が足した波形は溜めない
        stream.feed(wave(0.2))
        await asyncio.sleep(0.01)
    finally:
        task.cancel()

    sound = rig.streams[0]
    assert sound.interrupted is True
    assert sound.volumes[-1] == 0.0
    assert len(sound.fed) == 1
    assert not stream._chunks


async def test_aborted_stream_stops_what_it_already_played():
    # 偽の時計では見張りが一瞬で回るので、取り消しが届くまで鳴り終わらない長さにする
    rig = Rig(ticks=10**9)
    stream = rig.open_stream()
    task = await running(rig)
    try:
        stream.feed(wave(0.1))
        await wait_until(lambda: rig.streams and rig.streams[0].fed)
        stream.abort()
        await wait_until(lambda: rig.streams[0].stopped)
    finally:
        task.cancel()

    assert rig.streams[0].interrupted is True
    assert rig.streams[0].volumes[-1] == 0.0


async def test_stream_stops_with_fade_when_microphone_turns_on():
    def on_tick(rig, n):
        rig.probe.microphone = n >= 1

    rig = Rig(ticks=10, on_tick=on_tick)
    stream = rig.open_stream()
    stream.feed(wave(0.1))
    stream.close()

    await rig.drain(1)

    assert rig.streams[0].interrupted is True
    assert rig.streams[0].volumes[-1] == 0.0


async def test_stream_ducks_under_a_short_sound_and_restores():
    def on_tick(rig, n):
        rig.probe.output = n in (1, 2)

    rig = Rig(ticks=6, on_tick=on_tick)
    stream = rig.open_stream()
    stream.feed(wave(0.1))
    stream.close()

    await rig.drain(1)

    assert rig.streams[0].interrupted is False
    assert rig.streams[0].volumes == [0.8, 0.2, 0.8]


async def test_stream_and_wav_play_in_the_order_they_were_queued():
    rig = Rig()
    stream = rig.open_stream()
    rig.enqueue("after.wav")
    task = await running(rig)
    try:
        # 先に積んだ読み上げの音がまだ届いていなくても、後の wav は追い越さない
        await asyncio.sleep(0.02)
        assert [type(sound) for sound in rig.sounds] == [FakeStreamSound]
        stream.feed(wave(0.1))
        stream.close()
        await wait_until(lambda: rig.idle(2))
    finally:
        task.cancel()

    assert [type(sound) for sound in rig.sounds] == [FakeStreamSound, FakeSound]
    assert rig.sounds[1].path.name == "after.wav"


async def test_stream_that_cannot_open_plays_the_saved_wav_instead():
    rig = Rig(stream_open_error=OSError("no engine"))
    stream = rig.open_stream()
    stream.feed(wave(0.1))
    task = await running(rig)
    try:
        # wav は合成が終わるまで無い
        await asyncio.sleep(0.02)
        assert rig.sounds == []
        stream.close(Path("saved.wav"))
        await wait_until(lambda: rig.idle(1))
    finally:
        task.cancel()

    assert [sound.path.name for sound in rig.sounds] == ["saved.wav"]


async def test_stream_that_cannot_open_and_has_no_wav_plays_nothing():
    rig = Rig(stream_open_error=OSError("no engine"))
    stream = rig.open_stream()
    stream.feed(wave(0.1))
    stream.abort()
    rig.enqueue("next.wav")

    await rig.drain(1)

    assert [sound.path.name for sound in rig.sounds] == ["next.wav"]


async def test_gives_up_on_a_stream_whose_first_wave_never_comes(monkeypatch):
    monkeypatch.setattr(player_module, "STREAM_START_TIMEOUT_SECONDS", 0.05)
    rig = Rig()
    rig.open_stream()
    rig.enqueue("next.wav")

    await rig.drain(2)

    # 合成が固まっても、後ろの読み上げを止めない
    assert rig.streams[0].fed == []
    assert rig.streams[0].stopped is True
    assert rig.sounds[1].path.name == "next.wav"


async def test_plays_out_a_stream_whose_waves_stop_coming(monkeypatch):
    monkeypatch.setattr(player_module, "STREAM_STALL_SECONDS", 0.05)
    rig = Rig()
    stream = rig.open_stream()
    stream.feed(wave(0.1))

    await rig.drain(1)

    assert len(rig.streams[0].fed) == 1
    assert rig.streams[0].finished is True


async def test_stream_drops_audio_that_waited_too_long():
    rig = Rig(playback=PlaybackConfig(max_wait_seconds=60))
    stream = rig.open_stream()
    stream.feed(wave(0.1))
    stream.close()
    rig.now += 61

    await rig.drain(0)

    assert rig.sounds == []
    assert not stream._chunks


async def test_stream_opened_after_stop_is_never_queued():
    rig = Rig()
    accepted = rig.player.epoch
    rig.player.stop()

    stream = rig.open_stream(epoch=accepted)
    stream.feed(wave(0.1))
    await asyncio.sleep(0)

    assert rig.player.describe()["waiting"] == 0
    assert not stream._chunks


class Doubler:
    """加工の偽物。波形を2倍にして2ch にし、最後に響きを1フレーム足す。"""

    def __init__(self, fail_at=None):
        self.pushed = 0
        self.fail_at = fail_at

    def push(self, samples):
        self.pushed += 1
        if self.pushed == self.fail_at:
            raise ValueError("broken")
        return np.repeat(samples[:, None] * 2, 2, axis=1)

    def flush(self):
        return np.full((1, 2), 0.75, np.float32)


async def test_stream_is_shaped_before_it_is_played():
    rates = []

    def shape(sample_rate):
        rates.append(sample_rate)
        return Doubler()

    rig = Rig(shape=shape)
    stream = rig.open_stream()
    stream.feed(wave(0.1, frames=3))
    stream.feed(wave(0.2, frames=3))
    stream.close()

    await rig.drain(1)

    assert rates == [48000]
    fed = rig.streams[0].fed
    assert [pcm.shape for pcm in fed] == [(3, 2), (3, 2), (1, 2)]
    assert [float(pcm[0, 0]) for pcm in fed] == [np.float32(0.2), np.float32(0.4), 0.75]


async def test_stream_rounds_peaks_that_would_clip():
    rig = Rig()
    stream = rig.open_stream()
    stream.feed(np.array([0.5, 1.4, -1.4], np.float32))
    stream.close()

    await rig.drain(1)

    # 加工の有無によらず、1 を超える山は超えないように丸める。小さい音はそのまま
    (played,) = rig.streams[0].fed
    assert float(played[0, 0]) == 0.5
    assert 0.9 < float(played[1, 0]) < 1.0
    assert float(played[2, 0]) == -float(played[1, 0])


async def test_stream_plays_unshaped_when_shaping_fails():
    rig = Rig(shape=lambda sample_rate: Doubler(fail_at=2))
    stream = rig.open_stream()
    for value in (0.1, 0.2, 0.3):
        stream.feed(wave(value, frames=3))
    stream.close()

    await rig.drain(1)

    # 2つ目で加工が壊れたら、そこから先はそのまま鳴らす（響きも足さない）
    fed = rig.streams[0].fed
    assert [float(pcm[0, 0]) for pcm in fed] == [
        np.float32(0.2),
        np.float32(0.2),
        np.float32(0.3),
    ]


# --- 波形を積みながら鳴らす音（_StreamSound）---

RATE = 48000


class FakeOutput(InstantOutput):
    """再生位置（played）をテストが進める出力。fail を入れると、積むときに例外を投げる。"""

    def __init__(self):
        super().__init__(RATE)
        self.played = 0
        self.stops = 0
        self.fail = None

    def schedule(self, pcm):
        if self.fail is not None:
            raise self.fail
        super().schedule(pcm)

    def frames_played(self):
        return self.played

    def stop(self):
        super().stop()
        self.stops += 1

    @property
    def lengths(self):
        return [len(pcm) for pcm in self.scheduled]


def stream_sound(volume=0.8):
    output = FakeOutput()
    clock = [0.0]
    sound = player_module._StreamSound(output, RATE, volume, clock=lambda: clock[0])
    return sound, output, clock


def pcm(seconds):
    return np.zeros((int(seconds * RATE), 2), np.float32)


def test_stream_sound_starts_once_enough_is_buffered():
    sound, output, _ = stream_sound()

    sound.feed(pcm(0.3))
    assert output.started is False
    sound.feed(pcm(0.1))

    # 次の波形が届く前に鳴らし切って途切れないよう、0.4 秒たまってから鳴らし始める
    assert output.started is True
    assert output.volumes == [0.8]


def test_stream_sound_starts_a_short_sound_when_it_is_finished():
    sound, output, _ = stream_sound()

    sound.feed(pcm(0.1))
    sound.finish()

    assert output.started is True
    # 末尾が出力の手前に残ったまま止めないよう、無音を足す
    assert output.lengths == [int(0.1 * RATE), int(0.1 * RATE)]


def test_stream_sound_plays_until_everything_scheduled_is_played():
    sound, output, _ = stream_sound()
    sound.feed(pcm(0.5))
    assert sound.playing() is True
    assert sound.remaining() == WATCH_INTERVAL_SECONDS
    sound.finish()
    total = int(0.6 * RATE)

    output.played = total - 4800
    assert sound.playing() is True
    assert sound.remaining() == 0.1
    output.played = total
    assert sound.playing() is False


def test_stream_sound_with_nothing_fed_is_over_when_finished():
    sound, output, _ = stream_sound()

    sound.finish()

    assert sound.playing() is False
    assert output.started is False
    assert output.scheduled == []


def test_stream_sound_counts_a_gap_when_waves_come_too_late(caplog):
    sound, output, _ = stream_sound()
    sound.feed(pcm(0.5))
    # 積んだ 0.5 秒を鳴らし切ってから 0.25 秒、次が届かなかった
    output.played = int(0.75 * RATE)

    with caplog.at_level(logging.WARNING):
        sound.feed(pcm(0.5))
    sound.finish()

    assert "0.25 秒途切れました" in caplog.text
    # 途切れた分は、鳴らした長さに数えない
    assert sound.remaining() == 0.6
    output.played = int(1.35 * RATE)
    assert sound.playing() is False


def test_stream_sound_is_over_when_the_output_stops_running(caplog):
    sound, output, _ = stream_sound()
    sound.feed(pcm(0.5))
    sound.finish()

    output.stopped = True
    with caplog.at_level(logging.WARNING):
        assert sound.playing() is False

    assert "出力が止まった" in caplog.text


def test_stream_sound_gives_up_when_it_does_not_end_in_time():
    sound, _, clock = stream_sound()
    sound.feed(pcm(0.5))
    sound.finish()

    clock[0] = 0.6 + player_module.END_SLACK_SECONDS - 0.01
    assert sound.playing() is True
    # 再生位置が進まないまま、鳴り終わるはずの時刻を過ぎた
    clock[0] += 0.02
    assert sound.playing() is False


def test_stream_sound_releases_the_output_once():
    sound, output, _ = stream_sound()
    sound.feed(pcm(0.5))

    sound.stop()
    sound.stop()
    sound.feed(pcm(0.5))

    assert output.stops == 1
    assert sound.playing() is False
    assert output.lengths == [int(0.5 * RATE)]


def test_stream_sound_ends_when_the_output_raises(caplog):
    sound, output, _ = stream_sound()
    sound.feed(pcm(0.5))
    output.fail = RuntimeError("player started when in a disconnected state")

    with caplog.at_level(logging.WARNING):
        sound.feed(pcm(0.5))
    sound.finish()

    # 出力先が変わった後などに AVFoundation が例外を投げても、見張りを固めない
    assert "disconnected state" in caplog.text
    assert sound.playing() is False
    sound.stop()
    assert output.stops == 1


def test_a_hung_stream_sound_counts_as_finished_and_is_left_alone(monkeypatch):
    monkeypatch.setattr(player_module, "SOUND_CALL_TIMEOUT_SECONDS", 0.05)
    device = threading.Event()
    sound, output, _ = stream_sound()
    sound.feed(pcm(0.5))
    sound.finish()
    output.running = lambda: device.wait(5)
    try:
        assert sound.playing() is False
        sound.stop()  # 固まったスレッドが中にいる間は触らない
        assert output.stops == 0
    finally:
        device.set()


async def test_stream_sound_fades_the_volume_on_time(monkeypatch):
    monkeypatch.setattr(player_module, "RAMP_STEP_SECONDS", 0.001)
    output = FakeOutput()
    sound = player_module._StreamSound(output, RATE, 0.8)

    sound.set_volume(0.2, 0.2)
    await wait_until(lambda: output.volumes[-1] == 0.2)

    steps = output.volumes[1:]
    assert len(steps) > 3
    assert steps == sorted(steps, reverse=True)
    assert all(0.2 <= step <= 0.8 for step in steps)


async def test_stream_sound_continues_a_fade_from_where_the_last_one_got_to(monkeypatch):
    monkeypatch.setattr(player_module, "RAMP_STEP_SECONDS", 0.001)
    output = FakeOutput()
    sound = player_module._StreamSound(output, RATE, 0.8)

    # 下げている途中で「止める」が来た
    sound.set_volume(0.2, 10.0)
    await wait_until(lambda: len(output.volumes) > 3)
    reached = output.volumes[-1]
    sound.set_volume(0.0, 0.03)
    await wait_until(lambda: output.volumes[-1] == 0.0)

    after = output.volumes[output.volumes.index(reached) :]
    assert 0.7 < reached <= 0.8
    assert after == sorted(after, reverse=True)
    # 前の下げ先（0.2）へ飛ばず、そのときの音量から続けて下げる
    assert after[1] > 0.5
