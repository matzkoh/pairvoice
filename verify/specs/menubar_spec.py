"""メニューバーの見た目を決める純関数の性質。

/health の各項目を有限の候補から選ばせ、組み合わせのすべてで成り立つことを確かめる。
引数の int は候補の番号で、範囲は pre で絞る。
"""

from datetime import UTC, datetime

from pairvoice.menubar import (
    ICON_IDLE,
    ICON_MANUAL,
    ICON_MIC,
    ICON_MODEL_BROKEN,
    ICON_OUTPUT,
    ICON_UNREACHABLE,
    MenuItem,
    decide_icon,
    describe_state,
    menu_spec,
    models_line,
    warmup_item,
)

STATES = ("unloaded", "downloading", "loading", "loaded", "failed", "misconfigured")
BROKEN = ("failed", "misconfigured")
REASONS = (None, "manual", "microphone", "audio_output")
SPATIAL_MODES = ("outside", "orbit", "off")
NOW = datetime(2026, 8, 18, 3, 0, tzinfo=UTC)


def _health(
    llm: int,
    tts: int,
    reason: int,
    playing: bool = False,
    waiting: int = 0,
    update: int = 0,
    spatial: int = 0,
    orbit_menu: bool = False,
) -> dict:
    """update: 0 は項目なし（古いサーバー）、1 は最新、2 は新しい版あり。spatial: 0 は項目なし。"""
    health: dict = {
        "version": "0.8.0",
        "llm": {"state": STATES[llm], "detail": ""},
        "tts": {"state": STATES[tts], "detail": ""},
        "mute": {"reason": REASONS[reason], "until": None},
        "playback": {"playing": playing, "waiting": waiting},
    }
    if update:
        health["update"] = {"latest": "0.9.0", "available": update == 2}
    if spatial:
        health["spatial"] = {"mode": SPATIAL_MODES[spatial - 1], "orbit_menu": orbit_menu}
    return health


def _enabled_actions(rows: list[MenuItem]) -> list[str | None]:
    return [row.action for row in rows if row.enabled]


def icon_priority(reachable: bool, llm: int, tts: int, reason: int) -> bool:
    """届かない > モデルが壊れている > ミュートの理由、の順でアイコンを選ぶ。

    pre: 0 <= llm < 6 and 0 <= tts < 6 and 0 <= reason < 4
    post: __return__
    """
    if not reachable:
        expected = ICON_UNREACHABLE
    elif STATES[llm] in BROKEN or STATES[tts] in BROKEN:
        expected = ICON_MODEL_BROKEN
    else:
        expected = (ICON_IDLE, ICON_MANUAL, ICON_MIC, ICON_OUTPUT)[reason]
    return decide_icon(_health(llm, tts, reason) if reachable else None) == expected


def unreachable_leaves_restart_and_quit(updating: bool) -> bool:
    """届かないあいだに押せるのは再起動と終了だけ（どちらも届かないときこそ押したい）。

    post: __return__
    """
    return _enabled_actions(menu_spec(None, NOW, updating)) == ["restart", "quit"]


def enabled_actions_follow_playback_mute_and_update(
    reason: int, playing: bool, waiting: int, update: int, updating: bool
) -> bool:
    """届くあいだに押せる項目は決まった順に並び、条件つきのものは条件を満たすときだけ押せる。

    新しい版は出ていて更新中でないときだけ、止めるは鳴っているか待っているときだけ、
    ミュート解除は手動ミュートのときだけ。

    pre: 0 <= reason < 4 and 0 <= waiting <= 2 and 0 <= update < 3
    post: __return__
    """
    expected: list[str | None] = []
    if update == 2 and not updating:
        expected += ["update", "release_notes"]
    if playing or waiting > 0:
        expected.append("stop")
    expected += ["mute:15", "mute:30", "mute:60"]
    if REASONS[reason] == "manual":
        expected.append("unmute")
    expected += ["studio", "warmup", "check_update", "restart", "quit"]
    health = _health(0, 0, reason, playing, waiting, update)
    return _enabled_actions(menu_spec(health, NOW, updating)) == expected


def warmup_enabled_only_when_it_would_load(llm: int, tts: int) -> bool:
    """事前ロードは、読み込んでいない（か失敗した）モデルがあり、読み込み中でないときだけ押せる。

    pre: 0 <= llm < 6 and 0 <= tts < 6
    post: __return__
    """
    states = {STATES[llm], STATES[tts]}
    expected = not states & {"downloading", "loading"} and bool(states & {"unloaded", "failed"})
    health = _health(llm, tts, 0)
    row = next(row for row in menu_spec(health, NOW) if row.action == "warmup")
    return row == warmup_item(health) and row.enabled == expected


def display_rows_and_separators(reachable: bool, llm: int, tts: int, reason: int) -> bool:
    """先頭は状態の行（届けばモデルの行も）で押せない。区切り線は押せず、動作を持たない。

    pre: 0 <= llm < 6 and 0 <= tts < 6 and 0 <= reason < 4
    post: __return__
    """
    health = _health(llm, tts, reason) if reachable else None
    rows = menu_spec(health, NOW)
    heading = [MenuItem(describe_state(health, NOW), None, False)]
    if reachable:
        heading.append(MenuItem(models_line(health), None, False))
    separators = [row for row in rows if row.separator]
    return (
        rows[: len(heading)] == heading
        and len(separators) > 0
        and all(row.action is None and not row.enabled for row in separators)
    )


def warmup_label_by_priority(reachable: bool, llm: int, tts: int) -> bool:
    """事前ロードの文言は ダウンロード中 > ロード中 > ロード済み > 失敗 > それ以外 の順で決まる。

    pre: 0 <= llm < 6 and 0 <= tts < 6
    post: __return__
    """
    states = (STATES[llm], STATES[tts]) if reachable else ()
    if "downloading" in states:
        expected = "モデルをダウンロード中…"
    elif "loading" in states:
        expected = "モデルをロード中…"
    elif states == ("loaded", "loaded"):
        expected = "モデルはロード済み"
    elif "failed" in states:
        expected = "モデルをロードし直す"
    else:
        expected = "モデルを事前ロード"
    item = warmup_item(_health(llm, tts, 0) if reachable else None)
    return (item.label, item.action) == (expected, "warmup")


def spatial_items_are_exclusive(spatial: int, orbit_menu: bool) -> bool:
    """鳴らし方の項目は、今の方式にだけチェックが付き、押すと切れる。耳元は orbit_menu のときだけ出る。

    pre: 1 <= spatial < 4
    post: __return__
    """
    mode = SPATIAL_MODES[spatial - 1]
    rows = menu_spec(_health(3, 3, 0, spatial=spatial, orbit_menu=orbit_menu), NOW)
    by_label = {row.label: row for row in rows}
    expected = {"頭の外で鳴らす": "outside"} | ({"耳元で動かす": "orbit"} if orbit_menu else {})
    shown = {label for label in by_label if label in ("頭の外で鳴らす", "耳元で動かす")}
    return shown == set(expected) and all(
        by_label[label].enabled
        and by_label[label].checked == (mode == own)
        and by_label[label].action == ("spatial:off" if mode == own else f"spatial:{own}")
        for label, own in expected.items()
    )
