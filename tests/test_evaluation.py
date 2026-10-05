import json

from pairvoice import evaluation
from pairvoice.cli import main


def write_jsonl(path, records):
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )


def test_review_cases_take_the_latest_review_and_skip_archived(tmp_path):
    write_jsonl(
        tmp_path / "corpus.jsonl",
        [
            {"message_id": "aaaaaaaa-1", "input": "テストが通った", "summary": "通ったよ。"},
            {"message_id": "bbbbbbbb-2", "input": "ビルドが落ちた", "summary": "落ちたね。"},
            {"message_id": "cccccccc-3", "input": "アーカイブした", "summary": "済んだよ。"},
        ],
    )
    write_jsonl(
        tmp_path / "reviews.jsonl",
        [
            {"message_id": "aaaaaaaa-1", "verdict": "bad", "ideal": "古い理想"},
            {"message_id": "aaaaaaaa-1", "verdict": "good"},
            {"message_id": "bbbbbbbb-2", "verdict": "bad", "ideal": "ビルドが落ちたよ。"},
            {"message_id": "cccccccc-3", "verdict": "good"},
            {"message_id": "missing", "verdict": "good"},
        ],
    )
    write_jsonl(
        tmp_path / "archives.jsonl",
        [
            {"message_id": "bbbbbbbb-2", "archived": True},
            {"message_id": "bbbbbbbb-2", "archived": False},
            {"message_id": "cccccccc-3", "archived": True},
        ],
    )

    cases = evaluation.load_review_cases(tmp_path)

    assert cases == [
        evaluation.Case(
            id="good-aaaaaaaa", input="テストが通った", verdict="good", reference="通ったよ。"
        ),
        evaluation.Case(
            id="bad-bbbbbbbb",
            input="ビルドが落ちた",
            verdict="bad",
            reference="落ちたね。",
            ideal="ビルドが落ちたよ。",
        ),
    ]


def test_review_cases_are_empty_without_files(tmp_path):
    assert evaluation.load_review_cases(tmp_path) == []


def test_run_cases_judges_each_output_with_the_style():
    cases = [evaluation.Case(id="c1", input="入力"), evaluation.Case(id="c2", input="入力2")]
    outputs = iter(["テストが全部通ったから次に進めるよ。", "テストが全部通ったので次に進みます。"])

    results = evaluation.run_cases(cases, lambda system, prompt: next(outputs), "ルール", "casual")

    assert [r["all_pass"] for r in results] == [True, False]
    assert results[1]["reasons"] == {"desu_masu": "です・ます調の文末"}
    assert "verdict" not in results[0]


def test_summary_lists_violations_only_when_some_fail():
    passing = {
        "id": "c1",
        "output": "x",
        "rules": {n: True for n, _ in evaluation.checker.RULES},
        "all_pass": True,
    }
    failing = {
        **passing,
        "id": "c2",
        "rules": {**passing["rules"], "code_ident": False},
        "all_pass": False,
    }

    assert "違反例" not in evaluation.format_summary([passing])
    summary = evaluation.format_summary([passing, failing])
    assert "ALL_PASS      :  50.0%  (1/2)" in summary
    assert "[c2] x" in summary


def test_eval_command_reads_prompt_and_cases(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path))
    (tmp_path / "prompt.txt").write_text("ルール", encoding="utf-8")
    cases = tmp_path / "cases.tsv"
    cases.write_text("id\tinput\nc1\tテストが通った\n", encoding="utf-8")
    seen = []

    def fake_summarizer(base, max_tokens=None):
        def summarize(system, prompt):
            seen.append((system, prompt))
            return "テストが全部通ったから次に進めるよ。"

        return summarize

    monkeypatch.setattr(evaluation, "daemon_summarizer", fake_summarizer)
    out = tmp_path / "out.jsonl"

    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "eval",
            "--cases",
            str(cases),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    assert seen == [("ルール", "テストが通った")]
    assert json.loads(out.read_text(encoding="utf-8"))["all_pass"] is True
    assert "ALL_PASS      : 100.0%  (1/1)" in capsys.readouterr().out


def test_eval_command_reports_a_missing_prompt(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path))

    assert main(["--config", str(tmp_path / "missing.toml"), "eval"]) == 2
    assert "読めません" in capsys.readouterr().err


def test_review_cases_skip_lines_that_are_not_objects(tmp_path):
    (tmp_path / "reviews.jsonl").write_text('123\n"x"\n[]\n', encoding="utf-8")
    (tmp_path / "corpus.jsonl").write_text("null\n", encoding="utf-8")

    assert evaluation.load_review_cases(tmp_path) == []


def test_eval_command_reports_non_utf8_cases(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path))
    (tmp_path / "prompt.txt").write_text("ルール", encoding="utf-8")
    cases = tmp_path / "cases.tsv"
    cases.write_bytes(b"id\tinput\nc1\t\xff\n")

    code = main(["--config", str(tmp_path / "missing.toml"), "eval", "--cases", str(cases)])

    assert code == 2
    assert "読めません" in capsys.readouterr().err


def test_eval_command_checks_out_before_summarizing(tmp_path, monkeypatch, capsys):
    # 全ケースを要約し終えてから書けないと分かるのでは、待った時間が無駄になる
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path))
    (tmp_path / "prompt.txt").write_text("ルール", encoding="utf-8")
    (tmp_path / "blocker").write_text("", encoding="utf-8")
    summarized = []

    def fake_summarizer(base, max_tokens=None):
        return lambda system, prompt: summarized.append(prompt) or "ok"

    monkeypatch.setattr(evaluation, "daemon_summarizer", fake_summarizer)

    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "eval",
            "--out",
            str(tmp_path / "blocker" / "out.jsonl"),
        ]
    )

    assert code == 2
    assert summarized == []
    assert "書けません" in capsys.readouterr().err


def test_review_cases_survive_a_truncated_multibyte_write(tmp_path):
    # 追記の途中で切れた行（マルチバイトの途中）があっても、ほかの行は読む
    write_jsonl(
        tmp_path / "corpus.jsonl",
        [{"message_id": "m1", "input": "テストが通った", "summary": "通ったよ。"}],
    )
    write_jsonl(tmp_path / "reviews.jsonl", [{"message_id": "m1", "verdict": "good"}])
    with (tmp_path / "reviews.jsonl").open("ab") as f:
        f.write('{"message_id": "m2", "comment": "テ'.encode()[:-1])

    assert [c.id for c in evaluation.load_review_cases(tmp_path)] == ["good-m1"]


def test_eval_command_leaves_no_out_file_when_the_run_fails(tmp_path, monkeypatch):
    # 空の結果ファイルが残ると、走り切った評価に見える
    monkeypatch.setenv("PAIRVOICE_DATA_ROOT", str(tmp_path))
    (tmp_path / "prompt.txt").write_text("ルール", encoding="utf-8")

    def fake_summarizer(base, max_tokens=None):
        def summarize(system, prompt):
            raise RuntimeError("down")

        return summarize

    monkeypatch.setattr(evaluation, "daemon_summarizer", fake_summarizer)
    out = tmp_path / "results" / "out.jsonl"

    code = main(["--config", str(tmp_path / "missing.toml"), "eval", "--out", str(out)])

    assert code == 1
    assert not out.exists()
