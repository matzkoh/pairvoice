import json
import shutil

import pytest

from pairvoice.profiles import ProfileStore


def write_wav(path):
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")


def test_active_is_none_without_active_file(tmp_path):
    assert ProfileStore(tmp_path / "profiles").active() is None


def test_create_writes_profile_and_activates_it(tmp_path):
    store = ProfileStore(tmp_path / "profiles")

    profile = store.create(
        name="既定の声", caption="やわらかい声。", source="auto", write_reference=write_wav
    )

    assert profile.reference.read_bytes().startswith(b"RIFF")
    meta = json.loads((profile.reference.parent / "profile.json").read_text(encoding="utf-8"))
    assert meta["name"] == "既定の声"
    assert meta["caption"] == "やわらかい声。"
    assert meta["source"] == "auto"
    assert "created_at" in meta
    assert (tmp_path / "profiles" / "active").read_text(encoding="utf-8").strip() == profile.id
    assert store.active() == profile


def test_active_reads_caption_on_every_call(tmp_path):
    # studio が profile.json を書き換えたら、再起動なしで次の合成から効く
    store = ProfileStore(tmp_path / "profiles")
    profile = store.create(name="a", caption="一つ目。", source="auto", write_reference=write_wav)
    meta_path = profile.reference.parent / "profile.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["caption"] = "二つ目。"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    active = store.active()
    assert active is not None
    assert active.caption == "二つ目。"


def test_active_is_none_when_pointing_at_broken_profile(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    (root / "active").write_text("p-missing\n", encoding="utf-8")

    assert ProfileStore(root).active() is None


def test_active_is_none_when_reference_is_missing(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    profile = store.create(name="a", caption="声。", source="auto", write_reference=write_wav)
    profile.reference.unlink()

    assert store.active() is None


def test_active_rejects_ids_that_escape_the_root(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    (root / "active").write_text("../outside\n", encoding="utf-8")

    assert ProfileStore(root).active() is None


def test_active_is_none_when_active_file_is_not_utf8(tmp_path):
    root = tmp_path / "profiles"
    root.mkdir()
    (root / "active").write_bytes(b"\xff\xfe")

    assert ProfileStore(root).active() is None


@pytest.mark.parametrize("meta", ["[]", '"str"', "1", "null"])
def test_get_is_none_when_profile_json_is_not_an_object(tmp_path, meta):
    store = ProfileStore(tmp_path / "profiles")
    profile = store.create(name="a", caption="声。", source="auto", write_reference=write_wav)
    (profile.reference.parent / "profile.json").write_text(meta, encoding="utf-8")

    assert store.get(profile.id) is None


def test_create_leaves_nothing_when_writing_reference_fails(tmp_path):
    root = tmp_path / "profiles"
    store = ProfileStore(root)

    def fail(path):
        raise IsADirectoryError(path)

    with pytest.raises(IsADirectoryError):
        store.create(name="a", caption="声。", source="import", write_reference=fail)

    assert list(root.iterdir()) == []


def test_get_rejects_id_with_trailing_newline(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    profile = store.create(name="a", caption="声。", source="auto", write_reference=write_wav)
    # 末尾に改行を持つ名前のディレクトリも作れてしまうので、形の判定で弾く
    shutil.copytree(profile.reference.parent, tmp_path / "profiles" / (profile.id + "\n"))

    assert store.get(profile.id + "\n") is None
