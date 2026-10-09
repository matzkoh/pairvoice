import json

import pytest

from pairvoice.styles import Style, StyleInvalid, StyleNotFound, StyleStore


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_list_is_empty_without_file(tmp_path):
    assert StyleStore(tmp_path / "styles.json").all() == []


def test_get_returns_caption_and_sampler(tmp_path):
    path = tmp_path / "styles.json"
    write(
        path,
        {
            "styles": [
                {"name": "ゆっくり", "caption": None, "sampler": {"duration_scale": 1.2}},
                {"name": "ささやき", "caption": "ささやく。", "sampler": {"num_steps": 60}},
            ]
        },
    )

    assert StyleStore(path).get("ゆっくり") == Style(
        name="ゆっくり", caption=None, sampler={"duration_scale": 1.2}
    )
    assert StyleStore(path).get("ささやき").caption == "ささやく。"


def test_get_unknown_name_raises(tmp_path):
    path = tmp_path / "styles.json"
    write(path, {"styles": []})

    with pytest.raises(StyleNotFound):
        StyleStore(path).get("無い")


def test_int_is_accepted_for_float_sampler(tmp_path):
    path = tmp_path / "styles.json"
    write(path, {"styles": [{"name": "a", "sampler": {"duration_scale": 1}}]})

    assert StyleStore(path).get("a").sampler == {"duration_scale": 1.0}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"styles": [{"caption": "名前が無い"}]},
        {"styles": [{"name": "a", "caption": 1}]},
        {"styles": [{"name": "a", "sampler": {"num_step": 60}}]},
        {"styles": [{"name": "a", "sampler": {"num_steps": "60"}}]},
        {"styles": [{"name": "a", "sampler": [1]}]},
    ],
)
def test_malformed_file_raises_invalid(tmp_path, data):
    path = tmp_path / "styles.json"
    write(path, data)

    with pytest.raises(StyleInvalid):
        StyleStore(path).all()


def test_broken_json_raises_invalid(tmp_path):
    path = tmp_path / "styles.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(StyleInvalid):
        StyleStore(path).all()
