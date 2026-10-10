import struct
from pathlib import Path

import pytest

from pairvoice.data_api import MASTER_STEPS, REFERENCE_TEXTS, TAKE_GAP_SECONDS
from pairvoice.tts import SpeechResult, resolve_data_audio
from tests.test_engine import FakeTTS
from tests.test_server import build


def make_wav(samples: bytes, sample_rate: int = 24000) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, sample_rate, sample_rate * 2, 2, 16)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(samples))
        + b"WAVEfmt "
        + struct.pack("<I", 16)
        + fmt
        + b"data"
        + struct.pack("<I", len(samples))
        + samples
    )


@pytest.fixture
def client(tmp_path):
    _, client = build(data_root=tmp_path)
    return client


# ---- プロンプト ----


def test_prompt_is_empty_before_first_save(client):
    assert client.get("/prompt").json() == {"text": ""}


def test_put_prompt_saves_and_snapshots_what_was_written(client, tmp_path):
    assert client.put("/prompt", json={"text": "一つ目"}).json() == {"ok": True}
    client.put("/prompt", json={"text": "二つ目"})

    assert (tmp_path / "prompt.txt").read_text(encoding="utf-8") == "二つ目"
    assert client.get("/prompt").json() == {"text": "二つ目"}
    items = client.get("/prompt/history").json()["items"]
    # いま動いている版も履歴にある（新しい順）
    contents = [(tmp_path / "history" / item["name"]).read_text() for item in items]
    assert contents == ["二つ目", "一つ目"]
    assert items[0]["name"] == f"prompt-{items[0]['ts']}.txt"


def test_put_prompt_rejects_blank(client):
    assert client.put("/prompt", json={"text": "  \n"}).status_code == 422


def test_restore_prompt_writes_version_as_new_snapshot(client, tmp_path):
    client.put("/prompt", json={"text": "古い版"})
    old = client.get("/prompt/history").json()["items"][0]["name"]
    client.put("/prompt", json={"text": "新しい版"})

    assert client.post("/prompt/restore", json={"name": old}).json() == {"ok": True}

    assert client.get("/prompt").json() == {"text": "古い版"}
    assert len(client.get("/prompt/history").json()["items"]) == 3


@pytest.mark.parametrize(
    ("name", "status"),
    [
        ("../prompt.txt", 400),
        ("caption-2026-01-01T00-00-00-000Z.txt", 400),
        ("prompt-2026-01-01T00-00-00-000Z.txt", 404),
    ],
)
def test_restore_prompt_rejects_foreign_or_missing_versions(client, name, status):
    assert client.post("/prompt/restore", json={"name": name}).status_code == status


# ---- 読み辞書 ----


def test_dict_round_trips_rows_with_memo(client, tmp_path):
    rows = [
        {"from": "API", "to": "エーピーアイ", "memo": "略語"},
        {"from": "a", "to": "b", "memo": ""},
    ]

    assert client.put("/dict", json={"rows": rows}).json() == {"ok": True}

    assert client.get("/dict").json() == {"rows": rows}
    assert (tmp_path / "dict.tsv").read_text() == "API\tエーピーアイ\t略語\na\tb\t\n"


def test_dict_reads_crlf_and_missing_columns(client, tmp_path):
    (tmp_path / "dict.tsv").write_text("A\tえー\r\nメモだけの行\r\n", encoding="utf-8")

    assert client.get("/dict").json() == {
        "rows": [
            {"from": "A", "to": "えー", "memo": ""},
            {"from": "メモだけの行", "to": "", "memo": ""},
        ]
    }


@pytest.mark.parametrize(
    "row",
    [
        {"from": " ", "to": "x", "memo": ""},
        {"from": "a\tb", "to": "x", "memo": ""},
        {"from": "a", "to": "x\ny", "memo": ""},
    ],
)
def test_dict_rejects_rows_that_break_the_tsv(client, tmp_path, row):
    assert client.put("/dict", json={"rows": [row]}).status_code == 422
    assert not (tmp_path / "dict.tsv").exists()


# ---- スタイル ----


def test_styles_round_trip(client):
    items = [
        {"name": "ゆっくり", "caption": None, "sampler": {"duration_scale": 1.2}},
        {"name": "ささやき", "caption": "ささやく。", "sampler": {}},
    ]

    assert client.put("/styles", json={"items": items}).json() == {"ok": True}

    assert client.get("/styles").json() == {"items": items}


@pytest.mark.parametrize(
    "items",
    [
        [{"name": "a", "sampler": {}}, {"name": "a", "sampler": {}}],
        [{"name": " ", "sampler": {}}],
        [{"name": "a", "sampler": {"unknown_knob": 1}}],
        [{"name": "a", "sampler": {"num_steps": "many"}}],
    ],
)
def test_styles_reject_invalid_without_writing(client, tmp_path, items):
    response = client.put("/styles", json={"items": items})

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_style"
    assert not (tmp_path / "styles.json").exists()


# ---- 声 ----


def upload(client, name="手持ち", caption="", body=None):
    return client.post(
        "/profiles/upload",
        params={"name": name, "caption": caption},
        content=body if body is not None else make_wav(b"\x01\x00" * 10),
        headers={"Content-Type": "audio/wav"},
    )


def test_upload_creates_profile_and_activates_first_one(client):
    first = upload(client, "一つ目", " 明るく ")
    second = upload(client, "二つ目")

    assert first.status_code == 201
    assert first.json()["caption"] == "明るく"
    assert first.json()["source"] == "upload"
    listed = client.get("/profiles").json()
    # 使用中の声があれば、作っただけでは切り替えない
    assert listed["active"] == first.json()["id"]
    # 新しく作った順
    assert [p["id"] for p in listed["items"]] == [second.json()["id"], first.json()["id"]]


def test_upload_rejects_non_wav(client):
    response = upload(client, body=b"ID3 not a wav")

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_audio"


def test_create_from_takes_joins_with_silence(client, tmp_path):
    generations = tmp_path / "generations"
    generations.mkdir()
    (generations / "a.wav").write_bytes(make_wav(b"\x01\x00" * 3))
    (generations / "b.wav").write_bytes(make_wav(b"\x02\x00" * 3))

    response = client.post(
        "/profiles",
        json={
            "name": "混ぜた声",
            "caption": "低め",
            "takes": ["generations/a.wav", "generations/b.wav"],
        },
    )

    assert response.status_code == 201
    profile = response.json()
    assert profile["source"] == "design"
    audio = client.get(f"/profiles/{profile['id']}/audio")
    assert audio.headers["content-type"] == "audio/wav"
    gap = round(0.3 * 24000) * 2
    assert audio.content[44:] == b"\x01\x00" * 3 + bytes(gap) + b"\x02\x00" * 3


def test_create_from_takes_rejects_outside_and_other_profiles(client, tmp_path):
    reference = upload(client).json()["id"]

    outside = client.post("/profiles", json={"name": "x", "takes": ["../outside.wav"]})
    other = client.post(
        "/profiles", json={"name": "x", "takes": [f"profiles/{reference}/reference.wav"]}
    )

    assert outside.status_code == 404
    assert other.status_code == 400


def test_patch_updates_name_and_snapshots_caption(client):
    profile_id = upload(client, "前の名前").json()["id"]

    renamed = client.patch(f"/profiles/{profile_id}", json={"name": "新しい名前"})
    recaptioned = client.patch(f"/profiles/{profile_id}", json={"caption": "早口"})

    assert renamed.json()["name"] == "新しい名前"
    assert recaptioned.json() | {"created_at": ""} == {
        "id": profile_id,
        "name": "新しい名前",
        "caption": "早口",
        "source": "upload",
        "created_at": "",
    }
    (version,) = client.get(f"/profiles/{profile_id}/caption/history").json()["items"]
    assert version["name"].startswith("caption-")


def test_patch_requires_a_field(client):
    profile_id = upload(client).json()["id"]

    assert client.patch(f"/profiles/{profile_id}", json={}).status_code == 422
    assert client.patch(f"/profiles/{profile_id}", json={"name": " "}).status_code == 422
    assert client.patch("/profiles/p-missing", json={"name": "a"}).status_code == 404


def test_restore_caption(client):
    profile_id = upload(client).json()["id"]
    client.patch(f"/profiles/{profile_id}", json={"caption": "一つ目"})
    first = client.get(f"/profiles/{profile_id}/caption/history").json()["items"][0]["name"]
    client.patch(f"/profiles/{profile_id}", json={"caption": "二つ目"})

    response = client.post(f"/profiles/{profile_id}/caption/restore", json={"name": first})

    assert response.json() == {"ok": True}
    assert client.get("/profiles").json()["items"][0]["caption"] == "一つ目"
    bad = client.post(f"/profiles/{profile_id}/caption/restore", json={"name": "../x"})
    assert bad.status_code == 400


def test_activate_and_delete(client, tmp_path):
    first = upload(client, "一つ目").json()["id"]
    second = upload(client, "二つ目").json()["id"]

    in_use = client.delete(f"/profiles/{first}")
    assert in_use.status_code == 409
    assert in_use.json()["error"] == "profile_in_use"

    assert client.put("/profiles/active", json={"id": second}).json() == {"ok": True}
    assert client.delete(f"/profiles/{first}").json() == {"ok": True}

    assert not (tmp_path / "profiles" / first).exists()
    listed = client.get("/profiles").json()
    assert listed["active"] == second
    assert [p["id"] for p in listed["items"]] == [second]
    assert client.put("/profiles/active", json={"id": first}).status_code == 404


class TakeWritingTTS(FakeTTS):
    """読んだ文ごとに中身の違う wav を書く（つないだ順を確かめるため）。"""

    def __init__(self, data_root):
        super().__init__()
        self.data_root = data_root

    def resolve_audio(self, relative):
        return resolve_data_audio(self.data_root, relative)

    def speak(self, text, *args, **kwargs):
        super().speak(text, *args, **kwargs)
        relative = f"generations/made-{len(self.calls)}.wav"
        path = self.data_root / relative
        path.write_bytes(make_wav(bytes([len(self.calls), 0]) * 2))
        return SpeechResult(path=path, relative_path=relative, duration=0.1, peak_memory_gb=None)


def test_create_with_extend_reads_reference_texts_in_take_voice(tmp_path):
    tts = TakeWritingTTS(tmp_path)
    _, client = build(tts=tts, data_root=tmp_path)
    (tmp_path / "generations").mkdir()
    (tmp_path / "generations" / "take.wav").write_bytes(make_wav(b"\x09\x00" * 2))

    response = client.post(
        "/profiles",
        json={
            "name": "伸ばした声",
            "caption": "低め",
            "takes": ["generations/take.wav"],
            "extend": True,
            "rng_seed": 7,
        },
    )

    assert response.status_code == 201
    # 1文ずつ、テイクの声と同じ caption・種で、丁寧な段数で読む
    assert [call["text"] for call in tts.calls] == list(REFERENCE_TEXTS)
    assert {
        (call["caption"], call["sampler"]["rng_seed"], call["sampler"]["num_steps"])
        for call in tts.calls
    } == {("低め", 7, MASTER_STEPS)}
    take = (tmp_path / "generations" / "take.wav").resolve()
    assert all(call["mix"] == [(take, 1.0)] for call in tts.calls)
    audio = client.get(f"/profiles/{response.json()['id']}/audio").content
    gap = bytes(round(TAKE_GAP_SECONDS * 24000) * 2)
    made = [bytes([n, 0]) * 2 for n in (1, 2, 3)]
    assert audio[44:] == gap.join([b"\x09\x00" * 2, *made])


def test_create_with_extend_rejects_other_profiles_before_synthesizing(tmp_path):
    tts = TakeWritingTTS(tmp_path)
    _, client = build(tts=tts, data_root=tmp_path)
    reference = upload(client).json()["id"]

    response = client.post(
        "/profiles",
        json={"name": "x", "takes": [f"profiles/{reference}/reference.wav"], "extend": True},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_take"
    assert tts.calls == []


def test_reference_texts_and_steps_match_studio():
    # studio は進み具合を出すため、同じ文を自分で1本ずつ合成している。文がずれると、
    # 同じ声から作っても API と studio で参照音声が変わる
    source = (Path(__file__).parents[1] / "studio/web/src/features/profiles/mixSynth.ts").read_text(
        encoding="utf-8"
    )
    assert all(f"'{text}'" in source for text in REFERENCE_TEXTS)
    assert f"export const MASTER_STEPS = {MASTER_STEPS}\n" in source
