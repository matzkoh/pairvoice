"""読み上げの記録（コーパス）と、それへのレビュー・アーカイブ。

どれもデータの置き場所の追記専用の JSONL で、後の行ほど新しい。
- corpus.jsonl: フックが読み上げのたびに書く（入力・要約・音声）
- reviews.jsonl: studio の 👍 / 👎 と理想の出力。verdict "none" は取り消し
- archives.jsonl: アーカイブと、その解除

レビューとアーカイブを書くのは常駐サーバーだけにする（studio の画面は API を呼ぶ）。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from . import history

CORPUS_FILENAME = "corpus.jsonl"
REVIEWS_FILENAME = "reviews.jsonl"
ARCHIVES_FILENAME = "archives.jsonl"

# 同じファイルへの追記が重なると、改行を補う判定と書き込みの間に割り込まれて行がくっつく
_append_lock = threading.Lock()
# 読んだ行を (更新時刻, 大きさ) と組で覚える。studio はコーパスを数百件ずつのページで
# 並べて取りに来るので、覚えないとページの数だけ全行を解析し直し、要約や合成と競る
_read_cache: dict[Path, tuple[tuple[int, int], list[dict]]] = {}


def read_jsonl(path: Path) -> list[dict]:
    """壊れた行は飛ばす。無ければ空。返す行は覚えておいたものなので書き換えない。"""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _read_cache.get(path)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        # 追記の途中で切れた行はマルチバイトの途中で終わることがある。置換して読み、
        # その行は JSON として壊れているので下で落ちる
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    records = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    _read_cache[path] = (key, records)
    return records


def append_jsonl(path: Path, records: Iterable[dict]) -> None:
    """1回の呼び出しの行はまとめて1回で書く。

    前の書き込みが途中で切れて末尾に改行が無ければ、改行を足してから書く（そのまま続けると
    新しい行が壊れた行にくっつき、読む側がまとめて捨てる）
    """
    lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _append_lock, path.open("a+b") as handle:
        size = handle.seek(0, 2)
        if size > 0:
            handle.seek(size - 1)
            if handle.read(1) != b"\n":
                lines = "\n" + lines
        handle.write(lines.encode("utf-8"))


def latest_reviews(data_root: Path) -> dict[str, dict]:
    """message_id ごとの最新のレビュー（取り消しの "none" も含む）。"""
    return {
        r["message_id"]: r
        for r in read_jsonl(data_root / REVIEWS_FILENAME)
        if isinstance(r.get("message_id"), str) and r["message_id"]
    }


def archived_ids(data_root: Path) -> set[str]:
    archived: set[str] = set()
    for a in read_jsonl(data_root / ARCHIVES_FILENAME):
        message_id = a.get("message_id")
        if not isinstance(message_id, str) or not message_id:
            continue
        # 壊れた行は解除の側に倒す（勝手に隠さない）
        if a.get("archived") is True:
            archived.add(message_id)
        else:
            archived.discard(message_id)
    return archived


def corpus_time(ts: object) -> datetime | None:
    """corpus.jsonl の ts（フックが date で書いたローカル時刻 "2026-07-28 14:01:59"）。"""
    if not isinstance(ts, str):
        return None
    try:
        # オフセットの無い日時はローカル時刻として読む（astimezone がそうする）
        return datetime.fromisoformat(ts).astimezone()
    except ValueError:
        return None


def corpus_page(data_root: Path, limit: int, offset: int, prompt_since: datetime | None) -> dict:
    """新しい順の1ページに、レビュー・アーカイブ・旧プロンプトかどうかを突き合わせる。"""
    corpus = read_jsonl(data_root / CORPUS_FILENAME)
    reviews = latest_reviews(data_root)
    archived = archived_ids(data_root)
    # 新しい順の [offset, offset + limit) を、追記順の列の後ろから取る
    end = max(len(corpus) - offset, 0)
    # 返すページの分だけ突き合わせる（クライアントは全件を数ページに分けて取りに来る）
    items = [
        entry | _status(entry, reviews, archived, prompt_since)
        for entry in reversed(corpus[max(end - limit, 0) : end])
    ]
    return {
        "total": len(corpus),
        "items": items,
        "prompt_changed_at": None if prompt_since is None else history.iso_millis(prompt_since),
    }


def corpus_counts(data_root: Path, prompt_since: datetime | None) -> dict[str, int]:
    """studio のレビューの絞り込みごとの件数（studio の computeReviewCounts と同じ数え方）。

    アーカイブ済みはアーカイブだけに、旧プロンプトの読み上げは stale だけに数える。
    """
    reviews = latest_reviews(data_root)
    archived = archived_ids(data_root)
    counts = dict.fromkeys(("all", "unreviewed", "bad", "archived", "stale"), 0)
    for entry in read_jsonl(data_root / CORPUS_FILENAME):
        status = _status(entry, reviews, archived, prompt_since)
        if status["archived"]:
            counts["archived"] += 1
        elif status["stale"]:
            counts["stale"] += 1
        else:
            counts["all"] += 1
            if status["verdict"] is None:
                counts["unreviewed"] += 1
            elif status["verdict"] == "bad":
                counts["bad"] += 1
    return counts


def _status(
    entry: dict, reviews: dict[str, dict], archived: set[str], prompt_since: datetime | None
) -> dict:
    """読み上げの記録1件の、最新のレビュー・アーカイブ・旧プロンプトかどうか。"""
    message_id = entry.get("message_id")
    review = reviews.get(message_id) if isinstance(message_id, str) else None
    # 取り消し（none）は未レビューと同じ扱いにし、理想の出力も出さない
    verdict = review.get("verdict") if review is not None else None
    if verdict not in ("good", "bad"):
        verdict = None
    return {
        "verdict": verdict,
        "ideal": review.get("ideal") if review is not None and verdict is not None else None,
        "archived": message_id in archived,
        "stale": _is_stale(entry, prompt_since),
    }


def find_audio_path(data_root: Path, message_id: str) -> str | None:
    """その読み上げの音声（データの置き場所からの相対パス）。同じ ID なら後の行。"""
    for entry in reversed(read_jsonl(data_root / CORPUS_FILENAME)):
        if entry.get("message_id") == message_id:
            audio = entry.get("audio_path")
            return audio if isinstance(audio, str) and audio else None
    return None


def _is_stale(entry: dict, prompt_since: datetime | None) -> bool:
    # 「古い」と言うには時刻の証拠が要る。境界が引けないときと ts が読めないときは、
    # 古い側に落とさない（記録を勝手に隠すより、見えたまま人が判断できる方に倒す）
    if prompt_since is None:
        return False
    at = corpus_time(entry.get("ts"))
    return at is not None and at < prompt_since
