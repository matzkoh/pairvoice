import pytest

from pairvoice.corpus import CORPUS_FILENAME, read_jsonl
from pairvoice.narration import contains_japanese, has_speakable_content
from pairvoice.profiles import ProfileNotFound
from tests.fakes import FakeProbe
from tests.test_engine import FakeLLM, FakeTTS
from tests.test_server import build
from tests.test_tone import make_voice


class EnglishFirstLLM(FakeLLM):
    """最初の english 回だけ英語で答える。"""

    def __init__(self, english):
        super().__init__()
        self.english = english

    def generate(self, system, prompt, max_tokens=None):
        text = super().generate(system, prompt, max_tokens)
        return "All tests passed." if len(self.calls) <= self.english else text


def corpus(data_root):
    return read_jsonl(data_root / CORPUS_FILENAME)


def narrate(client, **body):
    return client.post("/narrate", json={"text": "テストが通った。", "message_id": "m1", **body})


@pytest.fixture
def data_root(tmp_path):
    (tmp_path / "prompt.txt").write_text("ルール\n")
    return tmp_path


def test_speaks_the_summary_in_the_voice_whose_tone_it_used_and_records_it(data_root):
    llm, tts = FakeLLM(), FakeTTS()
    _, client = build(llm=llm, tts=tts, data_root=data_root)
    voice = make_voice(data_root, "落ち着いた声")

    response = narrate(client, style="ゆっくり")

    assert response.status_code == 200
    assert response.json() == {
        "summary": "やった、テスト全部通ったよ。",
        "voice": voice.id,
        "relative_path": "generations/x.wav",
        "duration": 1.5,
    }
    assert llm.calls[0]["prompt"] == "テストが通った。"
    assert tts.calls[0]["text"] == "やった、テスト全部通ったよ。"
    assert tts.calls[0]["profile_id"] == voice.id
    assert tts.calls[0]["style"] == "ゆっくり"
    (record,) = corpus(data_root)
    assert record["message_id"] == "m1"
    assert record["input"] == "テストが通った。"
    assert record["summary"] == "やった、テスト全部通ったよ。"
    assert record["audio_path"] == "generations/x.wav"
    assert record["voice"] == voice.id


def test_without_a_voice_yet_records_no_voice(data_root):
    _, client = build(data_root=data_root)

    assert narrate(client).json()["voice"] is None
    assert "voice" not in corpus(data_root)[0]


def test_retries_until_the_summary_is_japanese(data_root):
    llm = EnglishFirstLLM(english=2)
    _, client = build(llm=llm, data_root=data_root)

    assert narrate(client).json()["summary"] == "やった、テスト全部通ったよ。"
    assert len(llm.calls) == 3


def test_gives_up_when_the_summary_never_becomes_japanese(data_root):
    tts = FakeTTS()
    _, client = build(llm=EnglishFirstLLM(english=99), tts=tts, data_root=data_root)

    response = narrate(client)

    assert response.status_code == 503
    assert response.json() == {"error": "no_japanese_summary"}
    assert tts.calls == []
    assert corpus(data_root) == []


def test_skips_fragments_with_nothing_to_read(data_root):
    llm = FakeLLM()
    _, client = build(llm=llm, data_root=data_root)

    response = narrate(client, text="</invoke>\n```")

    assert response.status_code == 400
    assert response.json() == {"error": "no_speakable_text"}
    assert llm.calls == []


def test_muted_does_not_summarize(data_root):
    llm = FakeLLM()
    _, client = build(llm=llm, probe=FakeProbe(microphone=True), data_root=data_root)

    response = narrate(client)

    assert response.status_code == 409
    assert response.json() == {"error": "muted", "reason": "microphone"}
    assert llm.calls == []


def test_missing_prompt(tmp_path):
    _, client = build(data_root=tmp_path)

    assert narrate(client).json() == {"error": "prompt_missing", "detail": "prompt.txt が無いか空"}


def test_records_the_summary_even_when_it_cannot_be_spoken(data_root):
    class MissingVoiceTTS(FakeTTS):
        def speak_sentences(self, *args, **kwargs):
            raise ProfileNotFound("x")

    _, client = build(tts=MissingVoiceTTS(), data_root=data_root)

    response = narrate(client)

    assert response.status_code == 404
    (record,) = corpus(data_root)
    assert record["summary"] == "やった、テスト全部通ったよ。"
    assert record["audio_path"] == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("</invoke>", False),
        ("```", False),
        ("<result>\n</result>", False),
        ("`ok`", True),
        ("テスト", True),
    ],
)
def test_has_speakable_content(text, expected):
    assert has_speakable_content(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("done", False), ("ok。", False), ("ひらがな", True), ("ｶﾀｶﾅ", True), ("漢字", True)],
)
def test_contains_japanese(text, expected):
    assert contains_japanese(text) is expected
