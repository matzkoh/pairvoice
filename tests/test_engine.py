import asyncio
import logging
import os
import threading
import time
import uuid

import pytest

import pairvoice.lifecycle as lifecycle_module
from pairvoice.config import Config, LLMConfig, MuteConfig, TTSConfig
from pairvoice.lifecycle import Engine, MutedError, Superseded
from pairvoice.mute import MuteController
from tests.fakes import FakeBackend, FakeProbe, gather_results


class FakeLLM(FakeBackend):
    def __init__(self, **kwargs):
        super().__init__(name="llm", model="fake/llm", **kwargs)
        self.calls = []

    def generate(self, system, prompt, max_tokens=None):
        self.calls.append({"system": system, "prompt": prompt, "max_tokens": max_tokens})
        return "やった、テスト全部通ったよ。"


class FakeTTS(FakeBackend):
    def __init__(self, caption=("フェイクの声。", "config"), sampler=None, **kwargs):
        super().__init__(name="tts", model="fake/tts", **kwargs)
        self.calls = []
        self._caption = caption
        # /health が申告する「いま渡しているサンプラー」。既定は1項目だけ入れて、
        # 「None を落とした結果」であることをテスト側から見えるようにしてある
        self._sampler = {"cfg_scale_speaker": 3.0} if sampler is None else sampler
        self.profile = None
        self.anchor_text = "候補に読ませる文です。"

    def resolve_caption(self, override=None):
        if override is not None and override.strip():
            return override.strip(), "request"
        return self._caption

    def resolve_sampler(self, override=None):
        merged = dict(self._sampler)
        if override:
            merged.update(override)
        return merged

    def describe_profile(self):
        return self.profile

    def speak(self, text, caption=None, sampler=None, design=False, profile_id=None):
        from pathlib import Path

        from pairvoice.tts import SpeechResult

        self.calls.append(
            {
                "text": text,
                "caption": caption,
                "sampler": sampler,
                "design": design,
                "profile_id": profile_id,
            }
        )
        return SpeechResult(
            path=Path("/tmp/pairvoice/generations/x.wav"),
            relative_path="generations/x.wav",
            duration=1.5,
            peak_memory_gb=999,
        )


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    # maintain() が合成音声を掃除するので、本物のデータの置き場所に触らせない
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path))


def make_engine(*, probe=None, mute_config=None, llm=None, tts=None):
    config = Config(
        llm=LLMConfig(model="fake/llm", idle_unload_seconds=60, load_timeout_seconds=1),
        tts=TTSConfig(model="fake/tts", idle_unload_seconds=60, load_timeout_seconds=1),
        mute=mute_config or MuteConfig(auto_microphone=True),
    )
    return Engine(
        config=config,
        llm_backend=llm or FakeLLM(),
        tts_backend=tts or FakeTTS(),
        mute=MuteController(config.mute, probe or FakeProbe()),
    )


async def test_summarize_loads_and_returns_text():
    llm = FakeLLM()
    engine = make_engine(llm=llm)

    text = await engine.summarize(system="ルール", prompt="作業ログ")

    assert text == "やった、テスト全部通ったよ。"
    assert llm.load_calls == 1
    assert llm.calls[0]["system"] == "ルール"


async def test_summarize_refuses_when_muted():
    engine = make_engine(probe=FakeProbe(microphone=True))

    with pytest.raises(MutedError) as error:
        await engine.summarize(system="s", prompt="p")

    assert error.value.reason == "microphone"


async def test_speak_refuses_when_muted():
    engine = make_engine(probe=FakeProbe(microphone=True))

    with pytest.raises(MutedError):
        await engine.speak("テスト")


async def test_speak_can_bypass_mute():
    tts = FakeTTS()
    engine = make_engine(probe=FakeProbe(microphone=True), tts=tts)

    result = await engine.speak("テスト", bypass_mute=True)

    assert result.relative_path == "generations/x.wav"
    assert tts.calls == [
        {"text": "テスト", "caption": None, "sampler": None, "design": False, "profile_id": None}
    ]


async def test_summarize_can_bypass_mute():
    engine = make_engine(probe=FakeProbe(microphone=True))

    assert await engine.summarize(system="s", prompt="p", bypass_mute=True)


async def test_summarize_that_is_not_droppable_survives_a_later_request():
    # 評価の要約は、後から来た読み上げの要約に追い越されても捨てられない
    llm = FakeLLM(load_delay=0.15)
    engine = make_engine(llm=llm)

    blocker = asyncio.create_task(engine.summarize(system="s", prompt="1"))
    await asyncio.sleep(0.03)
    evaluated = asyncio.create_task(engine.summarize(system="s", prompt="2", droppable=False))
    await asyncio.sleep(0.03)
    later = asyncio.create_task(engine.summarize(system="s", prompt="3"))

    results = await gather_results([blocker, evaluated, later])

    assert not any(isinstance(r, Exception) for r in results)
    assert [c["prompt"] for c in llm.calls] == ["1", "2", "3"]


async def test_summarize_rechecks_mute_after_waiting_in_queue():
    # summarize() の投入前チェックは、ミュートが有効化される前に通ってしまうことがある。先行の遅い呼び出しでキューを塞ぎ、待っている間にマイクを
    # 有効化すると、キューを抜けた瞬間の再判定で MutedError になるべき。
    probe = FakeProbe()
    llm = FakeLLM(load_delay=0.15)
    engine = make_engine(probe=probe, llm=llm)

    first_task = asyncio.create_task(engine.summarize(system="s", prompt="1"))
    await asyncio.sleep(0.03)  # first がロードを開始しキューを塞ぐのを待つ
    second_task = asyncio.create_task(engine.summarize(system="s", prompt="2"))
    await asyncio.sleep(0.03)  # second が投入前チェックを通過し待機に入るのを待つ
    probe.microphone = True

    first_result, second_result = await gather_results([first_task, second_task])

    assert first_result == "やった、テスト全部通ったよ。"
    assert isinstance(second_result, MutedError)
    assert second_result.reason == "microphone"


async def test_speak_rechecks_mute_after_waiting_in_queue():
    # speak 版。音声は droppable ではないので、
    # 追い越されずに実行はされるが、それでもキューを抜けた時点でミュートが
    # 有効なら鳴らしてはいけない。
    probe = FakeProbe()
    tts = FakeTTS(load_delay=0.15)
    engine = make_engine(probe=probe, tts=tts)

    first_task = asyncio.create_task(engine.speak("先行"))
    await asyncio.sleep(0.03)
    second_task = asyncio.create_task(engine.speak("後発"))
    await asyncio.sleep(0.03)
    probe.microphone = True

    first_result, second_result = await gather_results([first_task, second_task])

    assert first_result.relative_path == "generations/x.wav"
    assert isinstance(second_result, MutedError)
    assert second_result.reason == "microphone"


async def test_summarize_waits_for_a_short_sound_to_end():
    probe = FakeProbe(output=True)
    engine = make_engine(probe=probe, mute_config=MuteConfig(audio_output_wait_seconds=2))

    async def notification_ends():
        await asyncio.sleep(0.2)
        probe.output = False

    stopper = asyncio.create_task(notification_ends())
    text = await engine.summarize(system="s", prompt="p")
    await stopper

    assert text == "やった、テスト全部通ったよ。"


async def test_speak_refuses_while_other_audio_keeps_playing():
    engine = make_engine(
        probe=FakeProbe(output=True), mute_config=MuteConfig(audio_output_wait_seconds=0)
    )

    with pytest.raises(MutedError) as error:
        await engine.speak("テスト")

    assert error.value.reason == "audio_output"


async def test_in_queue_check_does_not_look_at_other_audio():
    # キューの中で音が止むのを待つと Runner を塞ぐ。音は再生の直前にフックが確かめる
    probe = FakeProbe()
    tts = FakeTTS(load_delay=0.15)
    engine = make_engine(probe=probe, tts=tts)

    first_task = asyncio.create_task(engine.speak("先行"))
    await asyncio.sleep(0.03)
    second_task = asyncio.create_task(engine.speak("後発"))
    await asyncio.sleep(0.03)
    probe.output = True

    _, second_result = await gather_results([first_task, second_task])

    assert second_result.relative_path == "generations/x.wav"


async def test_warmup_loads_models_sequentially_through_the_queue():
    # warmup() はグローバルな直列キューを経由するので、
    # llm のロード中に tts のロードが並行して始まってはいけない。
    llm = FakeLLM(load_delay=0.08)
    tts = FakeTTS(load_delay=0.08)
    engine = make_engine(llm=llm, tts=tts)

    warmup_task = asyncio.create_task(engine.warmup())
    await asyncio.sleep(0.03)  # llm はまだロード中のはず

    assert llm.load_calls == 1
    assert tts.load_calls == 0  # 並行していれば同時に1になっているはず

    result = await warmup_task

    assert sorted(result["started"]) == ["llm", "tts"]
    assert tts.load_calls == 1


async def test_waiting_summary_is_superseded_but_speech_is_not():
    # 要約2件がキューで待つ間、先行の読み上げに TTS のロードで Runner を塞がせておく。
    # 塞いでいないと、遅いマシンでは1件目の要約が待たずに走り、捨てられる要約が無くなる
    tts = FakeTTS(load_delay=0.15)
    engine = make_engine(tts=tts)

    slow = asyncio.create_task(engine.speak("ゆっくり"))
    await asyncio.sleep(0.03)
    first = asyncio.create_task(engine.summarize(system="s", prompt="1"))
    await asyncio.sleep(0.03)
    second = asyncio.create_task(engine.summarize(system="s", prompt="2"))
    third = asyncio.create_task(engine.speak("捨てられない"))

    results = await gather_results([slow, first, second, third])

    superseded = [r for r in results if isinstance(r, Superseded)]
    # <= 1 だと superseded が0件でも通ってしまい、summarize が droppable でなくなる
    # 退行を検出できない。ちょうど1件を求める
    assert len(superseded) == 1
    assert not any(isinstance(r, Exception) and not isinstance(r, Superseded) for r in results)


async def test_warmup_separates_started_and_already():
    engine = make_engine()

    first = await engine.warmup()
    second = await engine.warmup()

    assert sorted(first["started"]) == ["llm", "tts"]
    assert first["already"] == []
    assert second["started"] == []
    assert sorted(second["already"]) == ["llm", "tts"]


async def test_health_reports_states_and_mute_reason():
    engine = make_engine(probe=FakeProbe(microphone=True))

    health = engine.health()

    assert health["ok"] is True
    assert health["llm"]["state"] == "unloaded"
    assert health["tts"]["state"] == "unloaded"
    assert health["mute"] == {"active": True, "reason": "microphone", "until": None}
    assert health["queue"] == {"running": 0, "waiting": 0}
    assert health["dropped_recent"] == 0
    assert health["config_stale"] is False


async def test_health_last_used_is_iso_after_use():
    engine = make_engine()
    await engine.summarize(system="s", prompt="p")

    health = engine.health()

    assert health["llm"]["state"] == "loaded"
    assert health["llm"]["last_used"] is not None
    assert "T" in health["llm"]["last_used"]


async def test_health_reports_misconfigured_without_loading():
    # 参照音声が無い等の preflight 問題は、ロードを試みなくても
    # health() の時点で見えていなければならない。
    tts = FakeTTS(preflight_problem="ref_audio_missing")
    engine = make_engine(tts=tts)

    health = engine.health()

    assert health["tts"]["state"] == "misconfigured"
    assert health["tts"]["detail"] == "ref_audio_missing"
    assert tts.load_calls == 0


async def test_health_recovers_from_misconfigured_when_problem_clears():
    # 参照音声を後から設置したら、再起動なしで health() が
    # unloaded に戻る（自己回復）こと。
    tts = FakeTTS(preflight_problem="ref_audio_missing")
    engine = make_engine(tts=tts)
    assert engine.health()["tts"]["state"] == "misconfigured"

    tts.preflight_problem = None
    health = engine.health()

    assert health["tts"]["state"] == "unloaded"
    assert health["tts"]["detail"] == ""


async def test_health_does_not_reevaluate_loaded_model():
    # LOADED はロード経路が所有する状態なので、health() が
    # preflight() を再評価して misconfigured に書き換えてはいけない。
    tts = FakeTTS()
    engine = make_engine(tts=tts)
    await engine.speak("先行")  # tts を load 済みにする

    tts.preflight_problem = "ref_audio_missing"
    health = engine.health()

    assert health["tts"]["state"] == "loaded"


async def test_maintain_unloads_idle_models():
    llm = FakeLLM()
    engine = make_engine(llm=llm)
    await engine.summarize(system="s", prompt="p")
    engine._llm._idle_unload_seconds = 0  # 即座にアイドル扱いにする

    await engine.maintain()

    assert llm.unload_calls == 1
    assert engine.health()["llm"]["state"] == "unloaded"


async def test_maintain_prunes_old_generations_once_a_day(tmp_path):
    engine = make_engine()
    output = engine._config.tts.output_dir
    output.mkdir(parents=True)
    # tts.speak() が付ける名前（uuid4）でないと掃除の対象にならない
    old = output / f"{uuid.uuid4()}.wav"
    old.write_bytes(b"")
    eight_days_ago = time.time() - 8 * 86400
    os.utime(old, (eight_days_ago, eight_days_ago))

    await engine.maintain()
    assert not old.exists()

    # 次の掃除は1日後まで来ない
    old.write_bytes(b"")
    os.utime(old, (eight_days_ago, eight_days_ago))
    await engine.maintain()
    assert old.exists()


async def test_stop_cancels_background_tasks():
    engine = make_engine()

    await engine.start()
    tasks = list(engine._tasks)
    await engine.stop()

    assert engine._tasks == []
    assert all(task.done() for task in tasks)


async def test_maintain_loop_survives_exception_and_keeps_running(monkeypatch, caplog):
    # maintain() が例外を投げても _maintain_loop は死なず、
    # warning を出して次の周回でまた maintain() を呼び続けること。
    monkeypatch.setattr(lifecycle_module, "MAINTENANCE_INTERVAL_SECONDS", 0.01)
    engine = make_engine()

    calls = []
    original_maintain = engine.maintain

    async def flaky_maintain():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        await original_maintain()

    engine.maintain = flaky_maintain

    with caplog.at_level(logging.WARNING):
        await engine.start()
        await asyncio.sleep(0.05)
        await engine.stop()

    assert len(calls) >= 2
    assert any("maintain" in record.message for record in caplog.records)


async def test_speak_passes_caption_override_to_backend():
    tts = FakeTTS()
    engine = make_engine(tts=tts)

    await engine.speak("テスト", caption="試している声。")

    assert tts.calls == [
        {
            "text": "テスト",
            "caption": "試している声。",
            "sampler": None,
            "design": False,
            "profile_id": None,
        }
    ]


async def test_speak_forwards_design_flag_to_backend():
    tts = FakeTTS()
    engine = make_engine(tts=tts)

    await engine.speak("候補", caption="試す声。", design=True)

    assert tts.calls == [
        {"text": "候補", "caption": "試す声。", "sampler": None, "design": True, "profile_id": None}
    ]


async def test_speak_forwards_sampler_override_to_backend():
    tts = FakeTTS()
    engine = make_engine(tts=tts)

    await engine.speak("テスト", sampler={"num_steps": 60})

    assert tts.calls == [
        {
            "text": "テスト",
            "caption": None,
            "sampler": {"num_steps": 60},
            "design": False,
            "profile_id": None,
        }
    ]


class ThreadRecordingLLM(FakeLLM):
    """MLX を触る処理がどのスレッドで走ったかを記録する。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.threads = {}

    def load(self):
        self.threads["llm.load"] = threading.get_ident()
        super().load()

    def unload(self):
        self.threads["llm.unload"] = threading.get_ident()
        super().unload()

    def generate(self, system, prompt, max_tokens=None):
        self.threads["llm.generate"] = threading.get_ident()
        return super().generate(system, prompt, max_tokens)


class ThreadRecordingTTS(FakeTTS):
    def __init__(self, threads, **kwargs):
        super().__init__(**kwargs)
        self.threads = threads

    def load(self):
        self.threads["tts.load"] = threading.get_ident()
        super().load()

    def speak(self, text, caption=None, sampler=None, design=False, profile_id=None):
        self.threads["tts.speak"] = threading.get_ident()
        return super().speak(text, caption, sampler, design, profile_id)


async def test_mlx_work_runs_on_one_dedicated_thread():
    # MLX のストリームはスレッドごとに持たれる。モデルを読み込んだスレッドと別の
    # スレッドで生成すると「There is no Stream(gpu, N) in current thread」で落ちる
    llm = ThreadRecordingLLM()
    tts = ThreadRecordingTTS(llm.threads)
    engine = make_engine(llm=llm, tts=tts)

    # 既定のスレッドプールは複数のワーカーを持つ。並べて投げても同じ1本に集まること
    await asyncio.gather(*(asyncio.to_thread(time.sleep, 0.01) for _ in range(8)))
    await engine.summarize(system="s", prompt="p")
    await engine.speak("テスト", bypass_mute=True)
    await engine.summarize(system="s", prompt="p2")
    engine._llm._idle_unload_seconds = -1
    assert await engine._llm.unload_if_idle()

    assert set(llm.threads) == {"llm.load", "llm.generate", "llm.unload", "tts.load", "tts.speak"}
    assert len(set(llm.threads.values())) == 1
    assert threading.get_ident() not in llm.threads.values()
