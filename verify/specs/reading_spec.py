"""読み辞書の置き換え（apply_dict）の性質。

文字列の長さに上限を付け、その範囲のすべての入力で成り立つことを確かめる。3つ合わせて、
apply_dict が「上から順に、字句どおり（正規表現でなく）の全置換を重ねる」ことを言う。
"""

from pairvoice.reading import apply_dict


def no_rows_leave_text_as_is(text: str) -> bool:
    """
    pre: len(text) <= 4
    post: __return__
    """
    return apply_dict(text, []) == text


def one_row_replaces_every_literal_occurrence(text: str, source: str, target: str) -> bool:
    """
    pre: len(text) <= 3 and 1 <= len(source) <= 2 and len(target) <= 2
    post: __return__
    """
    return apply_dict(text, [(source, target)]) == text.replace(source, target)


def rows_apply_from_the_top(text: str, s1: str, t1: str, s2: str, t2: str) -> bool:
    """先の行の置換先も、後の行の置換元として置き換わる。

    pre: len(text) <= 3
    pre: 1 <= len(s1) <= 2 and len(t1) <= 2 and 1 <= len(s2) <= 2 and len(t2) <= 2
    post: __return__
    """
    return apply_dict(text, [(s1, t1), (s2, t2)]) == apply_dict(
        apply_dict(text, [(s1, t1)]), [(s2, t2)]
    )
