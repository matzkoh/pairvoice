import json
import re
from datetime import datetime

import pytest

from pairvoice.corpus import corpus_time
from pairvoice.history import version_time
from pairvoice.prompt import PromptStore
from tests.test_server import build


@pytest.fixture
def client(tmp_path):
    _, client = build(data_root=tmp_path)
    return client


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def entry(message_id, ts="2026-01-01 00:00:00", **extra):
    return {"ts": ts, "message_id": message_id, "input": "in", "summary": "out", **extra}


def by_id(client):
    return {i["message_id"]: i for i in client.get("/corpus").json()["items"]}


def test_corpus_is_newest_first_with_latest_review(client, tmp_path):
    write_jsonl(tmp_path / "corpus.jsonl", [entry("m1"), entry("m2")])
    write_jsonl(
        tmp_path / "reviews.jsonl",
        [
            {"ts": "t", "message_id": "m2", "verdict": "good", "ideal": ""},
            {"ts": "t", "message_id": "m2", "verdict": "bad", "ideal": "こう言ってほしかった"},
        ],
    )

    body = client.get("/corpus").json()

    assert body["total"] == 2
    assert [i["message_id"] for i in body["items"]] == ["m2", "m1"]
    assert (body["items"][0]["verdict"], body["items"][0]["ideal"]) == (
        "bad",
        "こう言ってほしかった",
    )
    assert (body["items"][1]["verdict"], body["items"][1]["ideal"]) == (None, None)


def test_counts_match_the_review_filters(client, tmp_path):
    write_jsonl(
        tmp_path / "corpus.jsonl",
        [
            entry("old", ts="2099-01-01 00:00:00"),
            entry("old-archived", ts="2099-01-01 00:00:00"),
            *(entry(m, ts="2099-12-31 00:00:00") for m in ("new", "good", "bad", "undone")),
        ],
    )
    (tmp_path / "prompt.txt").write_text("いまの版")
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "prompt-2099-06-01T00-00-00-000Z.txt").write_text("いまの版")
    write_jsonl(
        tmp_path / "reviews.jsonl",
        [
            {"ts": "t", "message_id": "good", "verdict": "good"},
            {"ts": "t", "message_id": "bad", "verdict": "bad"},
            {"ts": "t", "message_id": "undone", "verdict": "bad"},
            {"ts": "t", "message_id": "undone", "verdict": "none"},
        ],
    )
    write_jsonl(tmp_path / "archives.jsonl", [{"message_id": "old-archived", "archived": True}])

    assert client.get("/corpus/counts").json() == {
        "all": 4,
        "unreviewed": 2,
        "bad": 1,
        "archived": 1,
        "stale": 1,
    }


def test_corpus_pages_and_skips_broken_lines(client, tmp_path):
    (tmp_path / "corpus.jsonl").write_text(
        "\n".join([json.dumps(entry("a")), "{壊れた", json.dumps(entry("b"))]) + "\n"
    )

    body = client.get("/corpus", params={"limit": 1, "offset": 1}).json()

    assert body["total"] == 2
    assert [i["message_id"] for i in body["items"]] == ["a"]
    assert client.get("/corpus", params={"limit": -1}).status_code == 422


def test_review_none_resets_to_unreviewed_by_appending(client, tmp_path):
    write_jsonl(tmp_path / "corpus.jsonl", [entry("m")])

    client.post("/reviews", json={"message_id": "m", "verdict": "bad", "ideal": "理想"})
    assert by_id(client)["m"]["ideal"] == "理想"
    assert client.post("/reviews", json={"message_id": "m", "verdict": "none"}).json() == {
        "ok": True
    }

    assert (by_id(client)["m"]["verdict"], by_id(client)["m"]["ideal"]) == (None, None)
    lines = (tmp_path / "reviews.jsonl").read_text().splitlines()
    assert [json.loads(line)["verdict"] for line in lines] == ["bad", "none"]


def test_review_keeps_ideal_only_for_bad(client, tmp_path):
    client.post("/reviews", json={"message_id": "m", "verdict": "good", "ideal": "捨てる"})

    (record,) = [json.loads(line) for line in (tmp_path / "reviews.jsonl").read_text().splitlines()]
    assert record["ideal"] == ""
    # studio が書いていたころと同じ形の時刻
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", record["ts"])


@pytest.mark.parametrize(
    "body",
    [
        {"message_id": "m", "verdict": "maybe"},
        {"message_id": "", "verdict": "good"},
    ],
)
def test_review_rejects_invalid(client, tmp_path, body):
    assert client.post("/reviews", json=body).status_code == 422
    assert not (tmp_path / "reviews.jsonl").exists()


def test_archive_and_unarchive_by_appending(client, tmp_path):
    write_jsonl(tmp_path / "corpus.jsonl", [entry("a1")])

    assert client.post("/archives", json={"message_id": "a1", "archived": "yes"}).status_code == 422
    client.post("/archives", json={"message_id": "a1", "archived": True})
    assert by_id(client)["a1"]["archived"] is True
    client.post("/archives", json={"message_id": "a1", "archived": False})
    assert by_id(client)["a1"]["archived"] is False

    assert len((tmp_path / "archives.jsonl").read_text().splitlines()) == 2


def test_bulk_archive(client, tmp_path):
    write_jsonl(tmp_path / "corpus.jsonl", [entry("b1"), entry("b2")])

    assert (
        client.post("/archives/bulk", json={"message_ids": [], "archived": True}).status_code == 422
    )
    response = client.post("/archives/bulk", json={"message_ids": ["b1", "b2"], "archived": True})

    assert response.json() == {"ok": True, "count": 2}
    assert {m: i["archived"] for m, i in by_id(client).items()} == {"b1": True, "b2": True}


def test_append_repairs_a_cut_off_last_line(client, tmp_path):
    (tmp_path / "archives.jsonl").write_text('{"message_id": "x", "archi')

    client.post("/archives", json={"message_id": "a", "archived": True})

    lines = (tmp_path / "archives.jsonl").read_text().splitlines()
    assert json.loads(lines[1])["message_id"] == "a"


def test_corpus_audio(client, tmp_path):
    (tmp_path / "generations").mkdir()
    (tmp_path / "generations" / "a.wav").write_bytes(b"RIFF-fake")
    write_jsonl(
        tmp_path / "corpus.jsonl",
        [
            entry("ok", audio_path="generations/a.wav"),
            entry("evil", audio_path="../../etc/passwd"),
            entry("silent", audio_path=""),
        ],
    )

    audio = client.get("/corpus/ok/audio")
    assert (audio.status_code, audio.headers["content-type"], audio.content) == (
        200,
        "audio/wav",
        b"RIFF-fake",
    )
    for message_id in ("evil", "silent", "missing"):
        response = client.get(f"/corpus/{message_id}/audio")
        assert response.status_code == 404
        assert response.json() == {"error": "audio_not_found"}


def test_audio_serves_only_wav_under_data_root(client, tmp_path):
    (tmp_path / "generations").mkdir()
    (tmp_path / "generations" / "a.wav").write_bytes(b"RIFF-fake")
    (tmp_path / "corpus.jsonl").write_text("{}")

    assert client.get("/audio", params={"path": "generations/a.wav"}).content == b"RIFF-fake"
    for path in ("corpus.jsonl", "../outside.wav", "generations/none.wav"):
        assert client.get("/audio", params={"path": path}).status_code == 404


def test_corpus_marks_readings_before_current_prompt_as_stale(client, tmp_path):
    write_jsonl(
        tmp_path / "corpus.jsonl",
        [
            entry("old", ts="2099-01-01 00:00:00"),
            entry("new", ts="2099-12-31 00:00:00"),
            # 時刻が読めない行は「古い」と言い切る根拠が無いので古い側に落とさない
            entry("unknown", ts="こわれた"),
        ],
    )
    (tmp_path / "prompt.txt").write_text("いまの版")
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / "prompt-2099-06-01T00-00-00-000Z.txt").write_text("いまの版")

    body = client.get("/corpus").json()

    stale = {i["message_id"]: i["stale"] for i in body["items"]}
    assert stale == {"old": True, "new": False, "unknown": False}
    assert body["prompt_changed_at"] == "2099-06-01T00:00:00.000Z"


def test_current_since_moves_only_when_content_changes(tmp_path):
    store = PromptStore(tmp_path)
    history = tmp_path / "history"
    history.mkdir()
    at = iter(range(10))

    def save(text):
        store.path.write_text(text)
        (history / f"prompt-2099-01-01T00-00-0{next(at)}-000Z.txt").write_text(text)

    save("ひとつ前の版")
    save("いまの版")
    since = store.current_since()
    assert since is not None

    # 中身を変えずに保存し直しても境界は動かない
    save("いまの版")
    assert store.current_since() == since
    save("次の版")
    moved = store.current_since()
    assert moved is not None
    assert moved > since
    # API を通さず書き換えると、いつからの版か分からないので境界を引かない
    store.path.write_text("履歴に無い版")
    assert store.current_since() is None


def test_timestamps_of_history_and_corpus_share_a_number_line():
    assert version_time("prompt", "prompt-2026-07-28T05-03-13-373Z.txt") == datetime.fromisoformat(
        "2026-07-28T05:03:13.373+00:00"
    )
    assert version_time("prompt", "prompt-latest.txt") is None
    # corpus の ts はオフセットの無いローカル時刻
    assert corpus_time("2026-07-28 14:01:59") == datetime(2026, 7, 28, 14, 1, 59).astimezone()
    assert corpus_time("t") is None
    assert corpus_time(None) is None


def test_dict_test_applies_rows_in_order_without_saving(client, tmp_path):
    response = client.post(
        "/dict/test",
        json={
            "text": "A と B",
            "rows": [
                {"from": "A", "to": "B"},
                {"from": "B", "to": "ビー"},
                {"from": "", "to": "x"},
            ],
        },
    )

    assert response.json() == {"result": "ビー と ビー"}
    assert not (tmp_path / "dict.tsv").exists()


def test_audio_does_not_follow_symlinks_out_or_to_non_wav(client, tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.wav").write_bytes(b"secret")
    profile = tmp_path / "profiles" / "p-a"
    profile.mkdir(parents=True)
    (profile / "reference.wav").write_bytes(b"RIFF")
    (profile / "profile.json").write_text("{}")
    generations = tmp_path / "generations"
    generations.mkdir()
    (generations / "link.wav").symlink_to(outside / "secret.wav")
    (generations / "inner.wav").symlink_to(profile / "profile.json")

    assert client.get("/audio", params={"path": "profiles/p-a/reference.wav"}).status_code == 200
    for path in ("generations/link.wav", "generations/inner.wav", "profiles/p-a/profile.json"):
        assert client.get("/audio", params={"path": path}).status_code == 404
