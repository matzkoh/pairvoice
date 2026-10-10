import tempfile
from importlib.metadata import version
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pairvoice.config import Config, LLMConfig, MuteConfig, TTSConfig
from pairvoice.lifecycle import Engine, Superseded
from pairvoice.mute import MuteController
from pairvoice.profiles import ProfileNotFound
from pairvoice.server import create_app, is_local_request
from tests.fakes import FakeProbe
from tests.test_engine import FakeLLM, FakeTTS


def build(*, probe=None, llm=None, tts=None, mute_config=None, data_root=None):
    config = Config(
        llm=LLMConfig(model="fake/llm", load_timeout_seconds=1),
        # 既定の置き場所（利用者のデータ）を書き換えないよう、テストごとに空の場所を渡す
        tts=TTSConfig(
            model="fake/tts",
            load_timeout_seconds=1,
            output_dir=(data_root or Path(tempfile.mkdtemp())) / "generations",
        ),
        mute=mute_config or MuteConfig(),
    )
    engine = Engine(
        config=config,
        llm_backend=llm or FakeLLM(),
        tts_backend=tts or FakeTTS(),
        mute=MuteController(config.mute, probe or FakeProbe()),
    )
    return engine, TestClient(create_app(engine), base_url="http://127.0.0.1:17495")


def test_llm_returns_text():
    _, client = build()

    response = client.post("/llm", json={"system": "ルール", "prompt": "作業ログ"})

    assert response.status_code == 200
    assert response.json() == {"text": "やった、テスト全部通ったよ。"}


def test_llm_reports_mute_with_reason():
    _, client = build(probe=FakeProbe(microphone=True))

    response = client.post("/llm", json={"system": "s", "prompt": "p", "respect_mute": True})

    assert response.status_code == 409
    assert response.json() == {"error": "muted", "reason": "microphone"}


def test_llm_requires_system_and_prompt():
    _, client = build()

    assert client.post("/llm", json={"prompt": "p"}).status_code == 422


@pytest.mark.parametrize("max_tokens", [0, -1, 1025, 1.5, True])
def test_llm_rejects_invalid_max_tokens(max_tokens):
    llm = FakeLLM()
    _, client = build(llm=llm)

    response = client.post("/llm", json={"system": "s", "prompt": "p", "max_tokens": max_tokens})

    assert response.status_code == 422
    assert llm.calls == []


def test_llm_forwards_max_tokens():
    llm = FakeLLM()
    _, client = build(llm=llm)

    client.post("/llm", json={"system": "s", "prompt": "p", "max_tokens": 64})

    assert llm.calls[0]["max_tokens"] == 64


def test_llm_reports_load_failure_as_503():
    _, client = build(llm=FakeLLM(fail_load=True))

    response = client.post("/llm", json={"system": "s", "prompt": "p"})

    assert response.status_code == 503
    assert response.json()["error"] == "model_load_failed"


def test_hook_requests_do_not_wait_for_the_first_download():
    _, client = build(llm=FakeLLM(downloaded=False, download_delay=0.2))

    response = client.post("/llm", json={"system": "s", "prompt": "p", "wait_download": False})

    assert response.status_code == 503
    assert response.json()["error"] == "model_downloading"


def test_llm_reports_supersede_as_409(monkeypatch):
    engine, client = build()

    async def superseded(*args, **kwargs):
        raise Superseded

    monkeypatch.setattr(engine, "summarize", superseded)

    response = client.post("/llm", json={"system": "s", "prompt": "p"})

    assert response.status_code == 409
    assert response.json() == {"error": "dropped"}


def test_speak_returns_paths_and_duration():
    _, client = build()

    response = client.post("/speak", json={"text": "テスト"})

    assert response.status_code == 200
    body = response.json()
    assert body["relative_path"] == "generations/x.wav"
    assert body["path"].endswith("/generations/x.wav")
    assert body["duration"] == 1.5


def test_speak_reports_profile_missing_as_503():
    _, client = build(tts=FakeTTS(preflight_problem="profile_missing"))

    response = client.post("/speak", json={"text": "テスト"})

    assert response.status_code == 503
    assert response.json()["error"] == "profile_missing"


def test_speak_forwards_voice_to_backend():
    tts = FakeTTS()
    _, client = build(tts=tts)

    client.post("/speak", json={"text": "テスト", "voice": "p-other"})

    assert tts.calls[0]["profile_id"] == "p-other"


def test_speak_reports_unknown_profile_as_404():
    class MissingProfileTTS(FakeTTS):
        def speak(self, *args, **kwargs):
            raise ProfileNotFound("p-gone")

    _, client = build(tts=MissingProfileTTS())

    response = client.post("/speak", json={"text": "テスト", "voice": "p-gone"})

    assert response.status_code == 404
    assert response.json() == {"error": "profile_not_found"}


def test_speak_can_bypass_mute():
    _, client = build(probe=FakeProbe(microphone=True))

    muted = client.post("/speak", json={"text": "テスト"})
    bypassed = client.post("/speak", json={"text": "テスト", "bypass_mute": True})

    assert muted.status_code == 409
    assert muted.json() == {"error": "muted", "reason": "microphone"}
    assert bypassed.status_code == 200


def test_synthesize_ignores_mute():
    _, client = build(probe=FakeProbe(microphone=True))

    response = client.post("/synthesize", json={"text": "テスト"})

    assert response.status_code == 200
    assert response.json()["relative_path"] == "generations/x.wav"


def test_llm_ignores_mute_unless_asked():
    _, client = build(probe=FakeProbe(microphone=True))

    response = client.post("/llm", json={"system": "s", "prompt": "p"})

    assert "text" in response.json()


def test_speak_forwards_caption_to_backend():
    tts = FakeTTS()
    _, client = build(tts=tts)

    response = client.post("/speak", json={"text": "テスト", "caption": "試している声。"})

    assert response.status_code == 200
    assert tts.calls == [
        {
            "text": "テスト",
            "caption": "試している声。",
            "sampler": None,
            "design": False,
            "profile_id": None,
        }
    ]


def test_speak_forwards_design_flag_to_backend():
    tts = FakeTTS()
    _, client = build(tts=tts)

    response = client.post(
        "/synthesize", json={"text": "候補", "caption": "試す声。", "design": True}
    )

    assert response.status_code == 200
    assert tts.calls == [
        {"text": "候補", "caption": "試す声。", "sampler": None, "design": True, "profile_id": None}
    ]


def test_speak_without_caption_leaves_resolution_to_backend():
    tts = FakeTTS()
    _, client = build(tts=tts)

    client.post("/speak", json={"text": "テスト"})

    # サーバーは既定値を知らない。解決はバックエンドの1箇所に閉じる
    assert tts.calls == [
        {"text": "テスト", "caption": None, "sampler": None, "design": False, "profile_id": None}
    ]


def test_speak_forwards_sampler_overrides_to_backend():
    tts = FakeTTS()
    _, client = build(tts=tts)

    response = client.post(
        "/synthesize",
        json={"text": "テスト", "sampler": {"num_steps": 60, "rng_seed": 7}},
    )

    assert response.status_code == 200
    assert tts.calls == [
        {
            "text": "テスト",
            "caption": None,
            "sampler": {"num_steps": 60, "rng_seed": 7},
            "design": False,
            "profile_id": None,
        }
    ]


def test_speak_without_sampler_sends_nothing_to_backend():
    tts = FakeTTS()
    _, client = build(tts=tts)

    client.post("/speak", json={"text": "テスト"})

    # 上書きの解決はバックエンドの1箇所に閉じる。サーバーは何も足さない
    assert tts.calls == [
        {"text": "テスト", "caption": None, "sampler": None, "design": False, "profile_id": None}
    ]


def test_speak_drops_unset_sampler_keys():
    tts = FakeTTS()
    _, client = build(tts=tts)

    client.post("/synthesize", json={"text": "テスト", "sampler": {"num_steps": 60}})

    # 未指定は「モデル既定に任せる」。None を詰めて送ると config の値を潰してしまう
    assert tts.calls[0]["sampler"] == {"num_steps": 60}


def test_speak_rejects_unknown_sampler_key():
    _, client = build()

    response = client.post(
        "/synthesize",
        json={"text": "テスト", "sampler": {"num_stpes": 60}},
    )

    # 打ち間違いが黙って捨てられると「効かない理由が分からない」状態になる
    assert response.status_code == 422


def test_health_reports_effective_sampler():
    _, client = build()

    tts = client.get("/health").json()["tts"]

    assert tts["sampler"] == {"cfg_scale_speaker": 3.0}


def test_health_shape():
    _, client = build()

    body = client.get("/health").json()

    assert body["ok"] is True
    assert set(body) == {
        "ok",
        "llm",
        "tts",
        "mute",
        "queue",
        "playback",
        "dropped_recent",
        "config_stale",
    }
    assert body["llm"]["state"] == "unloaded"


def test_health_reports_current_caption_and_its_source():
    _, client = build()

    tts = client.get("/health").json()["tts"]

    assert tts["caption"] == "フェイクの声。"
    assert tts["caption_source"] == "config"


def test_health_reports_anchor_text_for_designing_voices():
    # studio のプロファイル作成は、既定の声を自動で作るときと同じ文を候補に読ませる
    tts = FakeTTS()
    _, client = build(tts=tts)

    assert client.get("/health").json()["tts"]["anchor_text"] == tts.anchor_text


def test_health_reports_active_profile():
    tts = FakeTTS()
    _, client = build(tts=tts)
    assert client.get("/health").json()["tts"]["profile"] is None

    tts.profile = {"id": "p-1", "name": "作った声", "source": "design"}

    assert client.get("/health").json()["tts"]["profile"] == tts.profile


def test_warmup_returns_202_with_started_and_already():
    _, client = build()

    first = client.post("/warmup")
    second = client.post("/warmup")

    assert first.status_code == 202
    assert sorted(first.json()["started"]) == ["llm", "tts"]
    assert sorted(second.json()["already"]) == ["llm", "tts"]


@pytest.mark.parametrize("payload", [{}, {"minutes": 0}, {"minutes": -5}, {"minutes": 481}])
def test_mute_rejects_invalid_minutes(payload):
    _, client = build()

    response = client.post("/mute", json=payload)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_minutes"


@pytest.mark.parametrize("minutes", [True, False, "30", 30.0])
def test_mute_rejects_non_integer_minutes(minutes):
    engine, client = build()

    response = client.post("/mute", json={"minutes": minutes})

    assert response.status_code == 422
    assert engine.mute.state().active is False


def test_mute_and_unmute_round_trip():
    _, client = build()

    muted = client.post("/mute", json={"minutes": 30})
    assert muted.status_code == 200
    assert muted.json()["active"] is True
    assert muted.json()["until"] is not None

    assert client.get("/health").json()["mute"]["reason"] == "manual"

    unmuted = client.post("/unmute")
    assert unmuted.json() == {"active": False}
    assert client.get("/health").json()["mute"]["active"] is False


def test_get_mute_reports_current_state():
    _, client = build(probe=FakeProbe(output=True))

    response = client.get("/mute")

    assert response.status_code == 200
    assert response.json() == {"active": True, "reason": "audio_output", "until": None}


def test_speak_queues_the_audio():
    engine, client = build()

    client.post("/speak", json={"text": "テスト"})

    assert client.get("/health").json()["playback"] == {"playing": False, "waiting": 1}
    assert client.post("/stop").json() == {"stopped": 1}
    assert engine.player.describe() == {"playing": False, "waiting": 0}


def test_synthesize_does_not_queue():
    engine, client = build()

    client.post("/synthesize", json={"text": "テスト"})

    assert engine.player.describe()["waiting"] == 0


def test_rejects_requests_from_foreign_pages():
    _, client = build()
    assert client.get("/health", headers={"host": "evil.example"}).status_code == 403
    # Host がローカルでも、外部のページからの単純な POST は Origin で断る
    assert client.post("/stop", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/stop", headers={"origin": "null"}).status_code == 403
    assert client.get("/health", headers={"host": "localhost:17495"}).status_code == 200
    assert client.post("/stop", headers={"origin": "http://127.0.0.1:17494"}).status_code == 200


@pytest.mark.parametrize(
    ("host", "origin", "expected"),
    [
        ("127.0.0.1:17495", None, True),
        ("LOCALHOST", None, True),
        ("[::1]:17495", "http://[::1]:17494", True),
        ("[::1]", None, True),
        (None, None, False),
        ("127.0.0.1.evil.example", None, False),
        ("127.0.0.1:17495", "http://127.0.0.1.evil.example", False),
    ],
)
def test_is_local_request(host, origin, expected):
    assert is_local_request(host, origin) is expected


def test_speak_forwards_mix_as_resolved_paths_and_weights():
    tts = FakeTTS()
    _, client = build(tts=tts)

    response = client.post(
        "/synthesize",
        json={
            "text": "テスト",
            "caption": "女性の声。",
            "mix": [
                {"audio": "generations/a.wav", "weight": 0.25},
                {"audio": "generations/b.wav", "weight": 0.75},
            ],
        },
    )

    assert response.status_code == 200
    assert tts.calls[0]["mix"] == [
        (Path("/data/generations/a.wav"), 0.25),
        (Path("/data/generations/b.wav"), 0.75),
    ]


def test_speak_reports_mix_outside_data_root_as_404():
    _, client = build()

    response = client.post(
        "/synthesize", json={"text": "テスト", "mix": [{"audio": "../secret.wav", "weight": 1}]}
    )

    assert response.status_code == 404
    assert response.json() == {"error": "audio_not_found"}


def test_speak_accepts_negative_mix_weight_for_extrapolation():
    tts = FakeTTS()
    _, client = build(tts=tts)

    response = client.post(
        "/synthesize",
        json={
            "text": "テスト",
            "mix": [
                {"audio": "generations/a.wav", "weight": 1.4},
                {"audio": "generations/b.wav", "weight": -0.4},
            ],
        },
    )

    assert response.status_code == 200
    assert [w for _, w in tts.calls[0]["mix"]] == [1.4, -0.4]


@pytest.mark.parametrize(
    "mix",
    [
        # 和が 0 以下では声にならない
        [
            {"audio": "generations/a.wav", "weight": 0.5},
            {"audio": "generations/b.wav", "weight": -0.5},
        ],
        # 伸ばしすぎ
        [{"audio": "generations/a.wav", "weight": 3}, {"audio": "generations/b.wav", "weight": -2}],
    ],
)
def test_speak_rejects_mix_that_cannot_be_a_voice(mix):
    _, client = build()

    response = client.post("/synthesize", json={"text": "テスト", "mix": mix})

    assert response.status_code == 422


def test_speaker_vector_returns_vector_without_mute():
    tts = FakeTTS()
    # 測るだけで音は出さないので、マイクが使われていても返す
    _, client = build(tts=tts, probe=FakeProbe(microphone=True))

    response = client.post("/speaker-vector", json={"audio": "generations/a.wav"})

    assert response.status_code == 200
    assert response.json() == {"vector": [0.5, -0.5]}
    assert tts.calls == [{"speaker_vector": Path("/data/generations/a.wav")}]


def test_speak_resolves_voice_as_profile_and_forwards_style():
    tts = FakeTTS()
    _, client = build(tts=tts)

    response = client.post(
        "/speak", json={"text": "テスト", "voice": "既定の声", "style": "ささやき"}
    )

    assert response.status_code == 200
    assert tts.calls[0]["profile_id"] == "既定の声"
    assert tts.calls[0]["style"] == "ささやき"


def test_speak_reports_unknown_style_as_404():
    from pairvoice.styles import StyleNotFound

    class MissingStyleTTS(FakeTTS):
        def speak(self, *args, **kwargs):
            raise StyleNotFound("無い")

    _, client = build(tts=MissingStyleTTS())

    response = client.post("/speak", json={"text": "テスト", "style": "無い"})

    assert response.status_code == 404
    assert response.json() == {"error": "style_not_found"}


def test_speak_reports_broken_styles_file():
    from pairvoice.styles import StyleInvalid

    class BrokenStyleTTS(FakeTTS):
        def speak(self, *args, **kwargs):
            raise StyleInvalid("壊れている")

    _, client = build(tts=BrokenStyleTTS())

    response = client.post("/speak", json={"text": "テスト", "style": "a"})

    assert response.status_code == 500
    assert response.json() == {"error": "style_invalid", "detail": "壊れている"}


def test_openapi_documents_version_and_every_field():
    _, client = build()

    spec = client.get("/openapi.json").json()

    assert spec["info"]["version"] == version("pairvoice")
    # 説明の無い項目を作らない（/docs が API の説明の正本）。FastAPI 自身の検証エラーの形は除く
    undocumented = [
        f"{schema}.{name}"
        for schema, body in spec["components"]["schemas"].items()
        if schema not in {"HTTPValidationError", "ValidationError"}
        for name, field in body.get("properties", {}).items()
        if not field.get("description")
    ]
    assert undocumented == []
    assert {"404", "500", "503"} <= spec["paths"]["/speak"]["post"]["responses"].keys()


def test_restart_answers_before_asking_launchd(monkeypatch):
    calls = []
    monkeypatch.setattr("pairvoice.server.launchd.restart", lambda: calls.append("restart"))
    _, client = build()

    response = client.post("/restart")

    assert response.status_code == 202
    assert response.json() == {"restarting": True}
    assert calls == ["restart"]


def test_shutdown_sends_sigterm_to_itself(monkeypatch):
    import os
    import signal

    killed = []
    monkeypatch.setattr("pairvoice.server.os.kill", lambda pid, sig: killed.append((pid, sig)))
    _, client = build()

    response = client.post("/shutdown")

    assert response.status_code == 202
    assert killed == [(os.getpid(), signal.SIGTERM)]


def test_studio_open_opens_the_page_this_server_serves(monkeypatch):
    opened = []
    monkeypatch.setattr(
        "pairvoice.studio_web.subprocess.Popen", lambda command, **kwargs: opened.append(command)
    )
    _, client = build()

    response = client.post("/studio/open")

    assert response.json() == {"url": "http://127.0.0.1:17495/studio/"}
    assert opened == [["open", "http://127.0.0.1:17495/studio/"]]


def test_eval_summarizes_given_cases_with_given_prompt(tmp_path):
    llm = FakeLLM()
    _, client = build(llm=llm, data_root=tmp_path)

    response = client.post(
        "/eval", json={"prompt": "ルール", "cases": [{"id": "c1", "input": "作業ログ"}]}
    )

    assert response.status_code == 200
    body = response.json()
    (result,) = body["results"]
    assert result["id"] == "c1"
    assert result["output"] == "やった、テスト全部通ったよ。"
    assert "ALL_PASS" in body["summary"]
    assert llm.calls[0]["system"] == "ルール"


def test_eval_defaults_to_saved_prompt(tmp_path):
    (tmp_path / "prompt.txt").write_text("保存したルール", encoding="utf-8")
    llm = FakeLLM()
    _, client = build(llm=llm, data_root=tmp_path)

    client.post("/eval", json={"cases": [{"id": "c1", "input": "ログ"}]})

    assert llm.calls[0]["system"] == "保存したルール"
