import pytest

from pairvoice.checker import evaluate


def failed_rules(text, style="casual"):
    return {name for name, ok in evaluate(text, style=style)["rules"].items() if not ok}


def test_ルールを満たす一文はすべて通る():
    assert failed_rules("テストが全部通ったから次に進めるよ。") == set()


def test_ルールを満たす二文もすべて通る():
    assert failed_rules("テストが全部通ったよ。次はレビューを待つだけだね。") == set()


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("テストが全部通ったので次に進みます。", "desu_masu"),
        ("テストが全部通ったから、もう大丈夫ね。", "noun_ne"),
        ("テストが落ちたから早く直してきな。", "imperative"),
        ("テストが落ちたから、すぐ直しな。", "imperative"),
    ],
)
def test_文末の違反を拾う(text, rule):
    assert rule in failed_rules(text)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("テストが全部通りました。次はレビューを待つだけだよ。", "desu_masu"),
        ("テストが全部通ったし大丈夫ね。次はレビューを待つだけだよ。", "noun_ne"),
        ("テストが落ちたから直しな。そのあとで見てあげるよ。", "imperative"),
    ],
)
def test_二文構成なら一文目の文末の違反も拾う(text, rule):
    assert rule in failed_rules(text)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("テストが通ったよ。レビューも済んだよ。マージしたよ。", "punct_count"),
        ("テストが全部通ったので次に進めるよ", "punct_count"),
        ("getSessionMessages を整理して読みやすくしたよ。", "code_ident"),
        ("僕のほうでテストを全部通しておいたよ。", "first_person"),
        ("テストが全部通ったから次に進めるよ🎉。", "length_emoji"),
        ("通ったよ。", "length_emoji"),
    ],
)
def test_文末以外のルールの違反を拾う(text, rule):
    assert rule in failed_rules(text)


def test_許可した略語はコード識別子として数えない():
    assert "code_ident" not in failed_rules("CI が通ったから PR をマージできるよ。")


def test_疑問のかなは命令口調として数えない():
    assert "imperative" not in failed_rules("テストが通ったから、次は何をしようかな。")


def test_文体を問わない既定ではです_ます調を違反にしない():
    # 雛形のプロンプトは「敬体か常体のどちらかに統一」。既定の評価器もそれに合わせる
    assert "desu_masu" not in failed_rules("テストが全部通ったので次に進みます。", style="any")


def test_casual_ではです_ます調を違反にする():
    assert "desu_masu" in failed_rules("テストが全部通ったので次に進みます。", style="casual")


def test_知らない文体はです_ます調を違反にしない():
    # 判定を厳しくする設定なので、綴りの誤りで黙って厳しくはしない
    assert "desu_masu" not in failed_rules("テストが全部通ったので次に進みます。", style="casul")


@pytest.mark.parametrize(
    "text",
    [
        "原因はキャッシュの設定でした。",
        "テストはまだ通っていません。",
        "次はレビューを確認してください。",
        "テストは全部通りませんでした。",
    ],
)
def test_casual_ではでした_ません_くださいも違反にする(text):
    assert "desu_masu" in failed_rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "原因はキャッシュの設定だったよ。",
        "テストはまだ通ってないよ。",
        "次はレビューを確認してね。",
    ],
)
def test_casual_なタメ口はです_ます調として数えない(text):
    assert "desu_masu" not in failed_rules(text)


@pytest.mark.parametrize(
    "text",
    ["これでやっと一段落ついたな。", "やっぱりキャッシュが原因だったよな。", "これで一安心だな。"],
)
def test_たな_よなは命令口調として数えない(text):
    assert "imperative" not in failed_rules(text)


@pytest.mark.parametrize("text", ["テストが落ちたから、すぐ直しな。", "もう遅いから早く寝な。"])
def test_命令の裸のなは引き続き拾う(text):
    assert "imperative" in failed_rules(text)


@pytest.mark.parametrize(
    "emoji", ["⭐", "⏳", "⌛", "⌚", "⏩", "⏸", "⬛", "⭕", "〰", "〽", "㊗", "㊙", "✔\ufe0f"]
)
def test_絵文字の範囲外だった記号も拾う(emoji):
    assert "length_emoji" in failed_rules(f"テストが全部通ったから次に進めるよ{emoji}。")


def test_日本語の記号は絵文字として数えない():
    assert "length_emoji" not in failed_rules("テストが全部通った〜、次は「レビュー」だよ。")


@pytest.mark.parametrize("symbol", ["⌘K", "⌥", "⌃", "⏎", "⌫", "⬅", "⬆"])
def test_キーボードや矢印の記号は絵文字として数えない(symbol):
    assert "length_emoji" not in failed_rules(f"{symbol} で開けるようにしておいたよ。")
