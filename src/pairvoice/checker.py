"""読み上げ用の要約を、要約プロンプトが要求する厳守ルールに照らして機械判定する。

ヒューリスティックなので、絶対評価ではなく候補どうしの相対比較に使う。

ルール:
  1. punct_count   : 句点・感嘆符・疑問符（。！？）が出力全体で1個または2個（二文構成まで許可）。
                     3個以上は違反。最後の句読点より後に文字が続く場合も違反。
  2. code_ident    : ファイルパス・関数名・変数名・コマンド名などのコード識別子を含まない。
                     ただし CI・PR のような短い大文字略語は許可する。
  3. imperative    : 命令・急かし口調（「〜しな」「〜してきな」「さっさと〜」文末「〜な」）を含まない。
                     3・4・7 の「文末」は、二文構成ならそれぞれの文の末尾を指す。
  4. noun_ne       : 名詞・形容詞＋「ね」止め（「大丈夫ね」「順調ね」）を含まない。
  5. length_emoji  : 約30文字程度（許容 10〜45 文字、二文構成なら上限は緩める）、絵文字を含まない。
  6. first_person  : 一人称「俺」「僕」「オレ」「ボク」を含まない。
  7. desu_masu     : です・ます調の文末（「〜ます。」「〜でした。」「〜ません。」「〜ください。」等）を含まない。
                     話者の好みに依存するので、config.toml の [eval] style = "casual" のときだけ
                     判定する。既定（"any"）は文体を問わず、常に PASS にする。

判定は単一のテキストに対して行い、各ルールについて pass/fail の bool を返す。
"""

from __future__ import annotations

import re

SENTENCE_END_CHARS = "。！？"
SENTENCE_PATTERN = re.compile(f"[^{SENTENCE_END_CHARS}]+[{SENTENCE_END_CHARS}]*")

# 短い技術略語のホワイトリスト（これらは code_ident 違反として数えない）
ALLOWED_ACRONYMS = {
    "CI",
    "PR",
    "QA",
    "UI",
    "UX",
    "API",
    "URL",
    "ID",
    "AI",
    "TTS",
    "OK",
    "NG",
    "TODO",
    "FYI",
    "ASMR",
}

# コード識別子らしさの検出パターン群
CODE_PATTERNS = [
    r"[a-zA-Z0-9_]+\.(sh|py|js|ts|tsx|jsx|json|md|yml|yaml|txt|log|rb|go|rs|c|cpp|h|java)\b",  # ファイル名
    r"/[a-zA-Z0-9_\-./]+/[a-zA-Z0-9_\-./]+",  # パスらしき文字列
    r"[a-zA-Z_][a-zA-Z0-9_]*\(\)",  # 関数呼び出し func()
    r"\b[a-z]+[A-Z][a-zA-Z0-9]*\b",  # camelCase
    r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b",  # snake_case
    r"`[^`]+`",  # バッククォート
    r"\$[A-Z_][A-Z0-9_]*",  # シェル変数
    r"--[a-zA-Z][a-zA-Z0-9\-]*",  # CLIオプション
]

EMOJI_PATTERN = re.compile(
    # 2300 台と 2B00 台は記号のブロックで、⌘ ⏎ や矢印のような技術文書の記号が多い。
    # 絵文字として出る字（⌚⌛ ⌨ ⏏ ⏩-⏳ ⏸-⏺ ⬛⬜ ⭐ ⭕）だけを拾う。
    # 〰 〽 ㊗ ㊙ は CJK の記号の中にある絵文字で、FE0F は絵文字として表示させる異体字セレクタ
    "[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff"
    "\u231a\u231b\u2328\u23cf\u23e9-\u23f3\u23f8-\u23fa\u2b1b\u2b1c\u2b50\u2b55"
    "\u3030\u303d\u3297\u3299\ufe0f]+",
    flags=re.UNICODE,
)

# 命令・急かし口調
# 文末の裸の「〜な」は動詞連用形（い段中心）に付く命令形。
# 「かな」（〜かな、疑問の“かな”）「だな」（断定の柔らかい念押し）「たな」（〜ついたな）
# 「よな」（〜だったよな）は命令ではないため除外。
IMPERATIVE_PATTERNS = [
    r"しな(?:よ|さい)?[。！？]?$",  # 「〜しな」文末
    r"してきな",
    r"さっさと",
    r"しろ[。！？]?$",
    r"(?<![かだではたよ])な[。！？]?$",  # 文末の裸の「〜な」（か/だ/で/は/た/よの後は除外）
]

# 名詞・形容詞＋「ね」止め（「〜だね」「〜だよ」は許可、直接「ね」で終わるものを検出）
# 「大丈夫ね」「順調ね」のように、形容動詞語幹/名詞に直接「ね」が付くケースを検出する。
# 動詞の活用語尾（う段: う く ぐ す つ ぬ ぶ む ゆ る／た・だ・て・で）や
# い形容詞語尾（い）、終助詞よ・ん の後に続く「ね」は自然な活用なので除外する。
NOUN_NE_PATTERN = re.compile(r"(?<![うくぐすつぬぶむゆるたてだでいんよら])ね[。！？]?$")


def split_sentences(text: str) -> list[str]:
    """句点・感嘆符・疑問符の直後で切る。文末に $ でアンカーする判定は文ごとに当てる。

    全体の末尾にだけ当てると、二文構成（ルール1で許可）の一文目の違反を見逃す。
    """
    return [s for s in SENTENCE_PATTERN.findall(text) if s.strip()]


def check_punct_count(text: str) -> tuple[bool, str]:
    positions = [i for i, ch in enumerate(text) if ch in SENTENCE_END_CHARS]
    if len(positions) == 0 or len(positions) > 2:
        return False, f"句読点数={len(positions)} (期待=1〜2)"
    last = positions[-1]
    trailing = text[last + 1 :].strip()
    if trailing:
        return False, f"句点後に文字列が続く: '{trailing}'"
    return True, ""


def check_code_ident(text: str) -> tuple[bool, str]:
    hits = []
    for pat in CODE_PATTERNS:
        for m in re.finditer(pat, text):
            token = m.group(0)
            if token.strip("`$-") in ALLOWED_ACRONYMS:
                continue
            hits.append(token)
    # 英単語連続（3文字以上のアルファベット列）もチェックするが、許可略語は除外
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9]{2,}", text):
        token = m.group(0)
        if token.upper() in ALLOWED_ACRONYMS or token in ALLOWED_ACRONYMS:
            continue
        # 既に上のパターンで拾われていなければ追加候補として拾う
        hits.append(token)
    hits = list(dict.fromkeys(hits))
    if hits:
        return False, f"コード識別子疑い: {hits}"
    return True, ""


def check_imperative(text: str) -> tuple[bool, str]:
    sentences = split_sentences(text)
    for pat in IMPERATIVE_PATTERNS:
        if any(re.search(pat, s) for s in sentences):
            return False, f"命令/急かし口調: pattern={pat}"
    return True, ""


def check_noun_ne(text: str) -> tuple[bool, str]:
    if any(NOUN_NE_PATTERN.search(s) for s in split_sentences(text)):
        return False, "名詞/形容詞+「ね」止めの疑い"
    return True, ""


def check_length_emoji(text: str) -> tuple[bool, str]:
    if EMOJI_PATTERN.search(text):
        return False, "絵文字を含む"
    n = len(text)
    positions = [i for i, ch in enumerate(text) if ch in SENTENCE_END_CHARS]
    upper = 60 if len(positions) >= 2 else 45
    if n < 10 or n > upper:
        return False, f"文字数={n} (期待 10〜{upper} 程度)"
    return True, ""


FIRST_PERSON_WORDS = ["俺", "僕", "オレ", "ボク"]


def check_first_person(text: str) -> tuple[bool, str]:
    hits = [w for w in FIRST_PERSON_WORDS if w in text]
    if hits:
        return False, f"一人称を含む: {hits}"
    return True, ""


# です・ます調の文末（style = "casual" のときだけ違反）
DESU_MASU_PATTERN = re.compile(
    r"(です|ます|ました|でした|ません|ください|ましょう|でしょう|ですよ|ますよ|ましたよ)[。！？]?$"
)


def check_desu_masu(text: str) -> tuple[bool, str]:
    if any(DESU_MASU_PATTERN.search(s) for s in split_sentences(text)):
        return False, "です・ます調の文末"
    return True, ""


RULES = [
    ("punct_count", check_punct_count),
    ("code_ident", check_code_ident),
    ("imperative", check_imperative),
    ("noun_ne", check_noun_ne),
    ("length_emoji", check_length_emoji),
    ("first_person", check_first_person),
    ("desu_masu", check_desu_masu),
]


def evaluate(text: str, style: str = "any") -> dict:
    """rules はルール名 → 合否、reasons は違反したルールだけの理由。

    desu_masu は style が "casual" のときだけ判定し、それ以外（知らない値も）は PASS にする。
    """
    text = text.strip()
    rules, reasons = {}, {}
    for name, fn in RULES:
        ok, reason = (True, "") if name == "desu_masu" and style != "casual" else fn(text)
        rules[name] = ok
        if not ok:
            reasons[name] = reason
    return {"text": text, "rules": rules, "reasons": reasons, "all_pass": all(rules.values())}
