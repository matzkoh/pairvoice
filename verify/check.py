"""verify/specs の事後条件を CrossHair で確かめ、すべてが全経路で確認できたときだけ通す。

crosshair check は反例が見つからなければ、時間切れで確かめきれなかった条件（Not confirmed）や
前提を満たす入力が無かった条件（中身を何も確かめていない）でも終了コード 0 で終わる。
コードが変わって網羅しきれなくなったら気づけるよう、条件ごとの結果を読んで判定する。

    uv run python verify/check.py [specs のファイル ...]
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SPECS = Path(__file__).parent / "specs"
CONFIRMED = "Confirmed over all paths."
# 条件1つあたりの上限。ふだんはどれも数十秒で網羅し終わる
PER_CONDITION_TIMEOUT = 300
RESULT = re.compile(r"^(?P<path>.+?):(?P<line>\d+): (?P<level>\w+): (?P<message>.*)$")


def conditions(path: Path) -> set[int]:
    """事後条件（docstring の post:）の行番号。CrossHair はこの行で結果を返す。"""
    lines = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.FunctionDef) or ast.get_docstring(node) is None:
            continue
        docstring = node.body[0]
        if not isinstance(docstring, ast.Expr) or not isinstance(docstring.value, ast.Constant):
            continue
        for offset, line in enumerate(str(docstring.value.value).splitlines()):
            if line.strip().startswith("post:"):
                lines.add(docstring.lineno + offset)
    return lines


def check(path: Path) -> list[str]:
    """確かめきれなかった条件を、行番号つきの説明で返す。"""
    expected = conditions(path)
    if not expected:
        return [f"{path}: 事後条件がない"]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crosshair",
            "check",
            "--report_all",
            "--per_condition_timeout",
            str(PER_CONDITION_TIMEOUT),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    results: dict[int, str] = {}
    for line in completed.stdout.splitlines():
        if (match := RESULT.match(line)) and Path(match["path"]).resolve() == path.resolve():
            results[int(match["line"])] = match["message"]

    problems = [
        f"{path}:{line}: {results.get(line, '結果が出なかった')}"
        for line in sorted(expected)
        if results.get(line) != CONFIRMED
    ]
    if completed.returncode not in (0, 1):
        problems.append(f"{path}: crosshair が終了コード {completed.returncode} で落ちた")
        problems.append(completed.stderr.strip())
    return problems


def main(argv: list[str]) -> int:
    paths = [Path(arg) for arg in argv] or sorted(SPECS.glob("*_spec.py"))
    # 記号実行は1コアしか使わないので、ファイルごとに並べて走らせる
    problems = []
    with ThreadPoolExecutor() as pool:
        for path, found in zip(paths, pool.map(check, paths), strict=True):
            print(f"{'NG' if found else 'OK'} {path}", flush=True)
            problems += found
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
