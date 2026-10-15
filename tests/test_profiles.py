import json
import shutil

import pytest

from pairvoice.profiles import ProfileInUse, ProfileNotFound, ProfileRejected, ProfileStore


def write_wav(path):
    path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")


def test_active_is_none_without_active_file(tmp_path):
    assert ProfileStore(tmp_path / "profiles").active() is None


def test_create_writes_profile_and_activates_it(tmp_path):
    store = ProfileStore(tmp_path / "profiles")

    profile = store.create(
        name="既定の声", caption="やわらかい声。", source="auto", write_reference=write_wav
    )

    assert (store.root / profile.id / "reference.wav").read_bytes().startswith(b"RIFF")
    meta = json.loads((store.root / profile.id / "profile.json").read_text(encoding="utf-8"))
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
    meta_path = (store.root / profile.id) / "profile.json"
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
    (store.root / profile.id / "reference.wav").unlink()

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
    (store.root / profile.id / "profile.json").write_text(meta, encoding="utf-8")

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
    shutil.copytree((store.root / profile.id), tmp_path / "profiles" / (profile.id + "\n"))

    assert store.get(profile.id + "\n") is None


def test_find_by_id_or_name_prefers_newest_of_same_name(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    older = store.create(
        name="同じ名前", caption="古い。", source="auto", write_reference=write_wav
    )
    newer = store.create(
        name="同じ名前", caption="新しい。", source="auto", write_reference=write_wav
    )
    # ID は秒単位の時刻で始まるので、同じ秒に作ると並びが乱数で決まる。作った順に並べ直す
    if newer.id < older.id:
        older, newer = newer, older

    assert store.find(older.id) == older
    assert store.find("同じ名前") == newer
    assert store.find("無い名前") is None
    assert [p.id for p in store.all()] == [older.id, newer.id]


def test_list_is_empty_without_directory(tmp_path):
    assert ProfileStore(tmp_path / "profiles").all() == []


def make_random(store, members=None):
    if members is None:
        members = [
            store.create(
                name=name, caption=f"{name}。", source="design", write_reference=write_wav
            ).id
            for name in ("甲", "乙")
        ]
    return store.create_random(name="ランダム", members=members), members


def test_random_profile_picks_a_member_each_time(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles")
    meta, members = make_random(store)
    store.activate(meta.id)

    assert store.get(meta.id) == meta
    assert meta.reference is None
    monkeypatch.setattr("pairvoice.profiles.random.sample", lambda seq, k: list(reversed(seq)))
    # 使用中でも、名前で選んでも、読むのはメンバーの声
    picked = [store.resolve(key) for key in (None, "ランダム")]
    assert [p.id if p is not None else None for p in picked] == [members[1], members[1]]
    # 使用中の表示はランダムの声のまま
    assert store.active() == meta


def test_random_profile_skips_members_that_are_gone(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    meta, members = make_random(store)
    shutil.rmtree(store.root / members[0])

    assert store.pick(meta).id == members[1]
    shutil.rmtree(store.root / members[1])
    with pytest.raises(ProfileNotFound):
        store.pick(meta)


@pytest.mark.parametrize("bad", ["one", "duplicate", "nested", "missing"])
def test_random_profile_rejects_invalid_members(tmp_path, bad):
    store = ProfileStore(tmp_path / "profiles")
    nested, members = make_random(store)
    members = {
        "one": members[:1],
        "duplicate": [members[0], members[0]],
        "nested": [members[0], nested.id],
        "missing": [members[0], "p-missing"],
    }[bad]

    error = ProfileNotFound if bad == "missing" else ProfileRejected
    with pytest.raises(error):
        store.create_random(name="不正", members=members)


def test_update_keeps_random_and_voice_fields_apart(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    meta, members = make_random(store)
    third = store.create(name="丙", caption="", source="design", write_reference=write_wav)

    updated = store.update(meta.id, name="気まぐれ", members=[*members, third.id])
    assert store.get(meta.id) == updated
    assert updated.members == (*members, third.id)
    with pytest.raises(ProfileRejected):
        store.update(meta.id, caption="早口")
    with pytest.raises(ProfileRejected):
        store.update(meta.id, tone="口調")
    with pytest.raises(ProfileRejected):
        store.update(members[0], members=members)


def test_member_of_random_profile_cannot_be_deleted(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    meta, members = make_random(store)
    store.activate(members[0])

    with pytest.raises(ProfileInUse, match="ランダム"):
        store.delete(members[1])
    store.delete(meta.id)
    store.delete(members[1])
    assert [p.id for p in store.all()] == [members[0]]


def make_shuffle(store, names=("甲", "乙", "丙")):
    members = [
        store.create(name=name, caption="", source="design", write_reference=write_wav).id
        for name in names
    ]
    return store.create_random(name="シャッフル", members=members, order="shuffle"), members


def test_shuffle_profile_picks_every_member_once_per_round(tmp_path, monkeypatch):
    store = ProfileStore(tmp_path / "profiles")
    meta, members = make_shuffle(store)
    assert store.require(meta.id).order == "shuffle"
    # 1巡目は並びのまま、2巡目は前の巡の最後（丙）から始まる並びを混ぜた結果にする
    rounds = iter([members] * 3 + [[members[2], members[0], members[1]]] * 3)
    monkeypatch.setattr("pairvoice.profiles.random.sample", lambda seq, k: list(next(rounds)))

    picked = [store.pick(meta).id for _ in range(6)]
    # 2巡目は同じ声が続かないよう、丙を後ろへ回す
    assert picked == [*members, members[0], members[1], members[2]]


def test_shuffle_profile_follows_member_changes(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    meta, _ = make_shuffle(store, names=("甲", "乙"))
    first = store.pick(meta).id

    # 残りの声を外すと、次の巡に移る
    third = store.create(name="丙", caption="", source="design", write_reference=write_wav)
    meta = store.update(meta.id, members=[first, third.id])
    assert store.pick(meta).id == third.id
    shutil.rmtree(store.root / first)
    shutil.rmtree(store.root / third.id)
    with pytest.raises(ProfileNotFound):
        store.pick(meta)


def test_order_is_only_for_random_profiles(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    meta, members = make_random(store)

    assert meta.order == "random"
    assert store.update(meta.id, order="shuffle") == store.get(meta.id)
    assert store.require(meta.id).order == "shuffle"
    with pytest.raises(ProfileRejected):
        store.update(meta.id, order="順番")
    with pytest.raises(ProfileRejected):
        store.update(members[0], order="shuffle")
