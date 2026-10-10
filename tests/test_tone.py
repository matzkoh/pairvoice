import json

import pytest

from pairvoice.profiles import ProfileStore
from pairvoice.prompt import SummaryPrompt
from tests.test_engine import FakeLLM
from tests.test_server import build


def make_voice(data_root, name):
    return ProfileStore(data_root / "profiles").create(
        name=name, caption="", source="upload", write_reference=lambda path: path.write_bytes(b"R")
    )


@pytest.fixture
def setup(tmp_path):
    llm = FakeLLM()
    _, client = build(data_root=tmp_path, llm=llm)
    return client, llm, tmp_path


def summarize(client, **body):
    return client.post("/llm", json={"prompt": "作業ログ", **body})


def test_without_tone_the_prompt_is_the_common_part_as_is(setup):
    client, llm, data_root = setup
    (data_root / "prompt.txt").write_text("共通\n")

    assert summarize(client).json()["voice"] is None
    assert llm.calls[-1]["system"] == "共通\n"


def test_voice_tone_replaces_the_default_tone(setup):
    client, llm, data_root = setup
    client.put("/prompt", json={"text": "共通\n"})
    client.put("/tone", json={"text": "既定の口調\n"})
    calm = make_voice(data_root, "落ち着いた声")  # 最初の声は使用中になる
    lively = make_voice(data_root, "元気な声")
    client.patch(f"/profiles/{lively.id}", json={"tone": "元気な口調"})

    assert summarize(client).json()["voice"] == calm.id
    assert llm.calls[-1]["system"] == "共通\n\n既定の口調\n"
    assert summarize(client, voice="元気な声").json()["voice"] == lively.id
    assert llm.calls[-1]["system"] == "共通\n\n元気な口調\n"
    # 空にすると既定の口調に戻る
    client.patch(f"/profiles/{lively.id}", json={"tone": "  \n"})
    summarize(client, voice=lively.id)
    assert llm.calls[-1]["system"] == "共通\n\n既定の口調\n"
    # system を渡せばそのまま使う
    assert summarize(client, system="そのまま", voice="元気な声").json()["voice"] is None
    assert llm.calls[-1]["system"] == "そのまま"


def test_unknown_voice_and_missing_prompt(setup):
    client, _, _ = setup

    assert summarize(client).json() == {
        "error": "prompt_missing",
        "detail": "prompt.txt が無いか空",
    }
    response = summarize(client, voice="無い声")
    assert (response.status_code, response.json()["error"]) == (404, "profile_not_found")


def test_default_tone_keeps_versions_and_restores(setup):
    client, _, _ = setup

    assert client.get("/tone").json() == {"text": ""}
    client.put("/tone", json={"text": "一"})
    client.put("/tone", json={"text": "二"})
    first = client.get("/tone/history").json()["items"][-1]["name"]
    assert client.post("/tone/restore", json={"name": first}).json() == {"ok": True}
    assert client.get("/tone").json() == {"text": "一"}
    assert len(client.get("/tone/history").json()["items"]) == 3


def test_voice_tone_is_part_of_the_profile(setup):
    client, _, data_root = setup
    voice = make_voice(data_root, "声")
    path = f"/profiles/{voice.id}"
    tone_of = lambda: next(  # noqa: E731
        i["tone"] for i in client.get("/profiles").json()["items"] if i["id"] == voice.id
    )

    assert tone_of() == ""
    assert client.patch(path, json={"tone": "一"}).json()["tone"] == "一"
    client.patch(path, json={"tone": "二"})
    first = client.get(f"{path}/tone/history").json()["items"][-1]["name"]
    assert client.post(f"{path}/tone/restore", json={"name": first}).json() == {"ok": True}
    assert tone_of() == "一"
    assert len(client.get(f"{path}/tone/history").json()["items"]) == 3
    assert (data_root / "profiles" / voice.id / "tone.txt").read_text() == "一"
    # 名前だけ変えても口調は消えない
    client.patch(path, json={"name": "新しい名前"})
    assert tone_of() == "一"
    assert client.get("/profiles/p-none/tone/history").status_code == 404


def test_composed_prompt_shows_what_the_summary_gets(setup):
    client, _, data_root = setup
    client.put("/prompt", json={"text": "共通"})
    voice = make_voice(data_root, "声")
    client.patch(f"/profiles/{voice.id}", json={"tone": "口調"})

    assert client.get("/prompt/composed").json() == {"text": "共通\n\n口調\n", "voice": voice.id}
    assert client.get("/prompt/composed", params={"voice": "無い声"}).status_code == 404


def test_eval_composes_the_candidate_with_the_voice_tone(setup):
    client, llm, data_root = setup
    client.put("/prompt", json={"text": "いまの共通"})
    voice = make_voice(data_root, "声")
    client.patch(f"/profiles/{voice.id}", json={"tone": "口調"})
    cases = [{"id": "c1", "input": "入力"}]

    client.post("/eval", json={"prompt": "候補", "voice": "声", "cases": cases})
    assert llm.calls[-1]["system"] == "候補\n\n口調\n"
    client.post("/eval", json={"cases": cases})
    assert llm.calls[-1]["system"] == "いまの共通\n\n口調\n"
    # 口調の候補も書き込まずに測れる
    client.post("/eval", json={"tone": "候補の口調", "cases": cases})
    assert llm.calls[-1]["system"] == "いまの共通\n\n候補の口調\n"


def test_readings_are_stale_by_the_prompt_of_their_own_voice(setup, monkeypatch):
    client, _, data_root = setup
    stamps = iter(f"2099-0{month}-01T00-00-00-000Z" for month in range(1, 10))
    monkeypatch.setattr(
        "pairvoice.history.iso_millis",
        lambda at: next(stamps).replace("T00-00-00-000Z", "T00:00:00.000Z"),
    )
    client.put("/prompt", json={"text": "共通"})  # 1月
    calm = make_voice(data_root, "落ち着いた声")
    lively = make_voice(data_root, "元気な声")
    client.patch(f"/profiles/{lively.id}", json={"tone": "元気な口調"})  # 2月
    lines = [
        {"ts": "2099-01-15 00:00:00", "message_id": "calm", "voice": calm.id},
        {"ts": "2099-01-15 00:00:00", "message_id": "lively", "voice": lively.id},
        {"ts": "2099-01-15 00:00:00", "message_id": "old"},
    ]
    (data_root / "corpus.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))

    stale = {i["message_id"]: i["stale"] for i in client.get("/corpus").json()["items"]}

    # 口調を変えたのは元気な声だけ
    assert stale == {"calm": False, "lively": True, "old": False}
    assert client.get("/corpus/counts").json()["stale"] == 1


def test_current_since_is_the_later_of_common_and_tone(tmp_path):
    prompt = SummaryPrompt(tmp_path)
    history = tmp_path / "history"
    history.mkdir()
    prompt.common.path.write_text("共通")
    (history / "prompt-2099-01-01T00-00-00-000Z.txt").write_text("共通")

    # 口調を一度も書いていなければ共通の部分だけで決まる
    january = prompt.current_since(None)
    assert january is not None
    prompt.default_tone.path.write_text("口調")
    (history / "tone-2099-03-01T00-00-00-000Z.txt").write_text("口調")
    march = prompt.current_since(None)
    assert march is not None
    assert march > january
    # 口調を API を通さず書き換えると、いつからか分からないので境界を引かない
    prompt.default_tone.path.write_text("書き換えた")
    assert prompt.current_since(None) is None
