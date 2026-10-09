from pairvoice.reading import apply_dict, load_dict


def test_load_dict_drops_memo_blank_source_and_lines_without_tab(tmp_path):
    path = tmp_path / "dict.tsv"
    path.write_text("A\tエー\tメモ\n見出し\n\nB\t\n\tから\nC\tシー\n", encoding="utf-8")

    assert load_dict(path) == [("A", "エー"), ("B", ""), ("C", "シー")]


def test_load_dict_strips_crlf(tmp_path):
    path = tmp_path / "dict.tsv"
    path.write_bytes("A\tエー\tメモ\r\nB\tビー\r\n".encode())

    assert load_dict(path) == [("A", "エー"), ("B", "ビー")]


def test_load_dict_missing_file_is_empty(tmp_path):
    assert load_dict(tmp_path / "dict.tsv") == []


def test_apply_dict_replaces_in_order_and_literally():
    rows = [("A", "エー"), ("&", "アンド"), ("#", "シャープ"), ("b", "\\&"), ("エー", "えー")]

    assert apply_dict("A & # b A", rows) == "えー アンド シャープ \\& えー"
