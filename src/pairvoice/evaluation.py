"""要約プロンプト・要約モデルの評価（`pairvoice eval`）。

ケースを要約し、checker の規則で判定して集計する。改善の作業そのものは利用者の
エージェントが行い（プラグインの tune スキル）、ここは測る道具だけを持つ。

要約は貪欲デコードで決定論的なので、同じ条件なら1回で足りる。評価を強くするならケースを増やす。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from . import checker

DEFAULT_CASES = Path(__file__).with_name("eval_cases.tsv")
# ルールごとに表示する違反例の数
EXAMPLES_PER_RULE = 3

Summarize = Callable[[str, str], str]


@dataclass(frozen=True)
class Case:
    id: str
    input: str
    # レビューから作ったケースだけが持つ。good は守るべき出力、bad は直す対象
    verdict: str | None = None
    reference: str | None = None
    ideal: str | None = None


def load_tsv_cases(path: Path) -> list[Case]:
    """`id<TAB>input` の TSV（1行目はヘッダ）を読む。"""
    cases = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        if not line.strip():
            continue
        case_id, _, text = line.partition("\t")
        cases.append(Case(id=case_id, input=text))
    return cases


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    # 追記の途中で切れた行はマルチバイトの途中で終わることがある。studio と同じく置換して
    # 読み、その行は JSON として壊れているので下で落ちる
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # 書きかけの行。studio の読み方と揃える
        if isinstance(record, dict):
            records.append(record)
    return records


def load_review_cases(data_root: Path) -> list[Case]:
    """studio のレビュー（👍 / 👎）をケースにする。アーカイブしたものは除く。

    どのファイルも追記専用で後の行が新しいので、message_id ごとに最後の行を採る。
    """
    reviews = {
        r["message_id"]: r for r in _read_jsonl(data_root / "reviews.jsonl") if r.get("message_id")
    }
    archived: set[str] = set()
    for a in _read_jsonl(data_root / "archives.jsonl"):
        if not a.get("message_id"):
            continue
        if a.get("archived") is True:
            archived.add(a["message_id"])
        else:
            archived.discard(a["message_id"])
    corpus = {
        c["message_id"]: c for c in _read_jsonl(data_root / "corpus.jsonl") if c.get("message_id")
    }

    cases = []
    for message_id, review in reviews.items():
        entry = corpus.get(message_id)
        if message_id in archived or entry is None:
            continue
        verdict = review.get("verdict")
        if verdict not in ("good", "bad"):
            continue
        cases.append(
            Case(
                id=f"{verdict}-{message_id[:8]}",
                input=entry["input"],
                verdict=verdict,
                reference=entry.get("summary"),
                ideal=review.get("ideal") or None,
            )
        )
    return cases


def judge(case: Case, output: str, style: str) -> dict:
    """1ケースの要約を規則で判定し、ケースの項目と合わせた記録にする。"""
    judged = checker.evaluate(output, style=style)
    record = {k: v for k, v in asdict(case).items() if v is not None}
    record["output"] = judged.pop("text")
    return record | judged


def run_cases(cases: Iterable[Case], summarize: Summarize, system: str, style: str) -> list[dict]:
    return [judge(case, summarize(system, case.input), style) for case in cases]


def format_summary(results: list[dict]) -> str:
    total = len(results)
    if total == 0:
        return "ケースがありません。"

    def rate(passed: int) -> str:
        return f"{100 * passed / total:5.1f}%  ({passed}/{total})"

    lines = [f"# ケース数: {total}", "", "## ルール別の合格率"]
    for name, _ in checker.RULES:
        lines.append(f"  {name:14s}: {rate(sum(r['rules'][name] for r in results))}")
    lines.append(f"  {'ALL_PASS':14s}: {rate(sum(r['all_pass'] for r in results))}")

    examples = []
    for name, _ in checker.RULES:
        failed = [r for r in results if not r["rules"][name]]
        if not failed:
            continue
        examples.append(f"  --- {name}（{len(failed)}件） ---")
        examples += [f"    [{r['id']}] {r['output']}" for r in failed[:EXAMPLES_PER_RULE]]
    if examples:
        lines += ["", f"## 違反例（ルールごとに最大{EXAMPLES_PER_RULE}件）", *examples]
    return "\n".join(lines)


def write_results(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8"
    )


def daemon_summarizer(base: str, max_tokens: int | None = None) -> Summarize:
    """常駐サーバーで要約する。読み込み済みのモデルを使うので、メモリを余分に使わない。"""
    from . import client

    def summarize(system: str, prompt: str) -> str:
        # 初回はモデルの読み込みを待つことがあるので、読み上げより長く待つ
        result = client.call(
            base,
            "/llm",
            body={
                "system": system,
                "prompt": prompt,
                "max_tokens": max_tokens,
                # 読み上げの要約に追い越されても1件も欠けさせない
                "droppable": False,
            },
            timeout=300,
        )
        if "text" not in result:
            raise RuntimeError(f"要約できませんでした: {result}")
        return result["text"]

    return summarize


def local_summarizer(model: str, max_tokens: int) -> Summarize:
    """指定のモデルをこのプロセスに読み込んで要約する（モデルの比較用）。

    常駐サーバーとは別に重みを載せるので、その分のメモリを使う。
    """
    from .config import LLMConfig
    from .lifecycle import limit_mlx_cache
    from .llm import MlxLmBackend

    limit_mlx_cache()
    backend = MlxLmBackend(LLMConfig(model=model, max_tokens=max_tokens))
    backend.load()
    return backend.generate
