"""長い素材の声を、話者の表現（トークンの列）1つに畳み込む。

30 秒の参照音声は、どの区間を選ぶかで声がぶれ、人が聴いて選ぶしかなかった。素材の
本人の声を全部トークンにして要約すれば、選ばずに素材全体の平均の声が得られる。

1. 素材の話者を分け、いちばん多く話す人の区間を全部使う（S/N や話し方では選ばない）
2. 本人の区間に前後の文脈を足し、同じファイル内で重なるか接する範囲をまとめる。
   長い範囲は文脈つきの窓に区切り、1窓ずつ雑音を除いてつなぐ
3. 本人の区間を切り戻し、長い無音を詰めてつないでから残響を除く
4. BLOCK_SECONDS ずつ話者の表現にする
5. 溜めたトークンを TOKENS 個に要約する

ボイスドラマ3本（約945秒）で、全体を先に雑音除去する方法と、区間を選んでから除く方法を比べた。
主要話者は同じで、発話は543.9秒から518.4秒、区間の IoU は0.934、フレーム一致率は0.962。
符号化に使った区間の IoU は0.85、共通部分の SI-SDR は9.2 dB、話者トークンの平均のコサイン類似度は0.990。
文脈をまとめる前の区間別処理で、雑音除去は198.1秒から136.5秒、残響除去は40.7秒から33.4秒、
全体は252.6秒から182.5秒になった。文脈をまとめると入力は771.4秒から670.4秒に減り、
雑音除去は116.6秒、全体は162.3秒になったが、最長45秒の音声を一度に Runner に渡していた。

合成の本体は話者のトークンを位置なしで参照する（順番のない袋）ので、似たトークンの塊ごとに
実在の代表（medoid）を選べば袋の要約になる。代表は平均で作らない（モデルが見たことのない
ぼやけたトークンになる）。ただし継続時間の予測は先頭の1トークンだけを見る（話す速さが決まる）
ので、先頭には各ブロックの先頭トークンのうち中心にいちばん近いものを置く。
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path

import numpy as np

from . import curate

_log = logging.getLogger(__name__)

RATE = curate.OUTPUT_RATE
# 要約したトークンの数。モデルの勧める参照音声の長さ（30 秒前後）のトークン数にそろえる
TOKENS = 180
# 話者の表現にする1回の長さ（ふだんの参照音声と同じくらい）
BLOCK_SECONDS = 26.0
# 文脈込みの上限。8秒窓を2秒ずつ重ねた3窓がちょうど収まる（8 + 6 + 6）。
# 内側の継ぎ目では前後2秒を捨て、16秒進むごとに Runner をほかの仕事に返す。
ENHANCE_SECONDS = 20.0
# 残響除去を1回に通す長さと、前後に足す文脈（残響除去の窓より長く）。数時間の声を丸ごと通すと、
# 途中の配列で数 GB になる
DEREVERB_SPAN_SECONDS = 900.0
DEREVERB_CONTEXT_SECONDS = 10.0
# 区間の中で、いちばん大きい所からこれより小さい 10ms は無音とみなす
SILENCE_DB = 40.0
# これより長い無音は、この長さに詰める
MAX_PAUSE_SECONDS = 0.3
KMEANS_ITERATIONS = 30


class FoldJobNotFound(Exception):
    """畳み込みの仕事が無い（ID の取り違え・止めた・常駐サーバーを再起動した）。"""


EncodeSpeaker = Callable[[np.ndarray], Awaitable[np.ndarray]]
Progress = Callable[[str, float], None]


@dataclass(frozen=True)
class Folded:
    # (TOKENS, 次元) の話者の表現
    tokens: np.ndarray
    # 試聴と、表現を使えないときの代わりにする、中心にいちばん近いブロックの音
    reference: np.ndarray
    report: dict
    # 表現を作った合成モデルの名前
    model: str


def trim_silence(x: np.ndarray) -> np.ndarray:
    """MAX_PAUSE_SECONDS より長い無音を詰める。"""
    hop = RATE // 100
    frames = x[: len(x) // hop * hop].reshape(-1, hop)
    if not len(frames):
        return frames.reshape(-1)
    level = 10 * np.log10((frames**2).mean(axis=1) + 1e-12)
    voiced = level > level.max() - SILENCE_DB
    # 直前の声のフレームからの距離（無音が続いた数）が上限を超えたフレームを落とす
    index = np.arange(len(voiced))
    last_voiced = np.maximum.accumulate(np.where(voiced, index, -1))
    return frames[index - last_voiced <= int(MAX_PAUSE_SECONDS * 100)].reshape(-1)


def _clean_spans(intervals: list[tuple[int, int]], start: int, end: int, context: int):
    """時刻順の本人の区間を、同じファイルの文脈つき範囲と切り戻す位置にまとめる。単位はサンプル。"""
    pieces = []
    lo = hi = start
    for a, b in intervals:
        a, b = max(start, a), min(end, b)
        if a >= b:
            continue
        left, right = max(start, a - context), min(end, b + context)
        if pieces and hi < left:
            yield lo, hi, pieces
            pieces = []
        if not pieces:
            lo = left
        hi = max(hi, right)
        pieces.append((a, b))
    if pieces:
        yield lo, hi, pieces


def _clean_window(start: int, stop: int, cursor: int, limit: int, context: int):
    """次の入力の両端と、文脈を除いて採用する区間 [cursor, end) の末尾。"""
    left = max(start, cursor - context)
    right = min(stop, left + limit)
    end = right if right == stop else right - context
    return left, right, end


async def clean_pieces(
    analysis: curate.Analysis, enhance: curate.Enhance, offload: curate.Offload, progress: Progress
) -> list[np.ndarray]:
    material = analysis.material
    spans = []
    for source, pieces in groupby(
        analysis.pieces, key=lambda p: material.source_at((p.start + p.end) / 2)
    ):
        start = round(source.offset * RATE)
        intervals = [(int(p.start * RATE), int(p.end * RATE)) for p in pieces]
        spans.extend(
            _clean_spans(
                intervals,
                start,
                start + round(source.seconds * RATE),
                round(curate.ENHANCE_CONTEXT_SECONDS * RATE),
            )
        )

    total = sum(hi - lo for lo, hi, _ in spans)
    done = 0
    progress("clean", 0.0)
    speech = []
    limit = round(ENHANCE_SECONDS * RATE)
    context = round(curate.ENHANCE_CONTEXT_SECONDS * RATE)
    for lo, hi, intervals in spans:
        mono = await offload(curate.louder_channel, material.audio48[lo:hi])
        cursor = lo
        # 区間は開始時刻順。次の本人区間にも使う窓だけ残し、長い連続発話を丸ごと保持しない。
        windows: deque[tuple[int, int, np.ndarray]] = deque()
        for a, b in intervals:
            while windows and windows[0][1] <= a:
                windows.popleft()
            while cursor < b:
                left, right, end = _clean_window(lo, hi, cursor, limit, context)
                cleaned = await enhance(mono[left - lo : right - lo])
                windows.append((cursor, end, cleaned[cursor - left : end - left]))
                done += end - cursor
                cursor = end
                progress("clean", done / total)
            parts = [
                audio[max(a, start) - start : min(b, end) - start]
                for start, end, audio in windows
                if start < b and a < end
            ]
            piece = parts[0] if len(parts) == 1 else np.concatenate(parts)
            trimmed = await offload(trim_silence, piece)
            if len(trimmed):
                speech.append(trimmed)
    return speech


async def remove_reverb(
    pieces: list[np.ndarray], dereverb: curate.Dereverb, offload: curate.Offload, progress: Progress
) -> list[np.ndarray]:
    """区間をつないだ声の響きを除き、区間ごとに切り戻す。pieces は空にする（つないだ写しと二重に抱えない）。"""
    progress("dereverb", 0.0)
    bounds = np.cumsum([len(p) for p in pieces])[:-1]
    speech = await offload(np.concatenate, pieces)
    pieces.clear()
    span, context = int(DEREVERB_SPAN_SECONDS * RATE), int(DEREVERB_CONTEXT_SECONDS * RATE)
    dry = np.empty_like(speech)
    for start in range(0, len(speech), span):
        lo, hi = max(0, start - context), min(len(speech), start + span + context)
        end = min(len(speech), start + span)

        def advance(fraction: float, start: int = start, end: int = end) -> None:
            progress("dereverb", (start + fraction * (end - start)) / len(speech))

        # 8 秒の窓を1秒だけ重ねる。境目は窓で混ぜつつ、長い素材の二重処理を減らす。
        y = await curate.remove_reverb(
            speech[lo:hi], dereverb, offload, overlap=curate.DEREVERB_RATE, progress=advance
        )
        dry[start:end] = y[start - lo : end - lo]
    return np.split(dry, bounds)


def speech_blocks(pieces: list[np.ndarray]) -> list[np.ndarray]:
    """本人の区間の無音を詰めてつなぎ、BLOCK_SECONDS ずつに切る。

    無音は響きを除いたあとにも詰める（響きが消えると、声の切れ目の無音が伸びる）。
    区間ごとの声の大きさは、ふだんの参照音声と同じくそろえる（素材の録り方で十数 dB 違い、
    小さいまま符号化すると、モデルがふだん見ている参照音声と大きさの違う声を表現にする）。
    """
    parts = []
    for piece in pieces:
        trimmed = trim_silence(piece)
        if len(trimmed):
            parts.append(curate.level_piece(trimmed))
    speech = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    step = int(BLOCK_SECONDS * RATE)
    # 端数が半分に満たなければ捨てる（短すぎる表現は、ほかのブロックと性質が違う）
    return [
        curate.limit_peaks(speech[i : i + step], RATE)
        for i in range(0, len(speech) - step // 2, step)
    ]


def _distances(doubled: np.ndarray, squared: np.ndarray, centers: np.ndarray) -> np.ndarray:
    distances = squared[:, None] - doubled @ centers.T
    distances += (centers**2).sum(1)[None]
    return distances


def _seed_centers(x: np.ndarray, k: int, seed: int) -> np.ndarray:
    """k-means++ で初期中心を選ぶ。"""
    rng = np.random.default_rng(seed)
    centers = [x[rng.integers(len(x))]]
    nearest = None
    for _ in range(k - 1):
        distances = ((x - centers[-1]) ** 2).sum(1)
        if nearest is None:
            nearest = distances
        else:
            np.minimum(nearest, distances, out=nearest)
        centers.append(x[rng.choice(len(x), p=nearest / nearest.sum())])
    return np.stack(centers)


def _update_centers(
    x: np.ndarray, centers: np.ndarray, label: np.ndarray, rows: np.ndarray
) -> np.ndarray:
    from scipy.sparse import csr_matrix

    k = len(centers)
    # (塊, 点) の疎行列で、各塊の全次元をまとめて足す。列は元の点の順に並ぶ。
    if centers.dtype in (np.float32, np.float64):
        membership = csr_matrix(
            (np.ones(len(x), dtype=centers.dtype), (label, rows)), shape=(k, len(x))
        )
        sums = membership @ x
    else:
        sums = np.zeros_like(centers)
        np.add.at(sums, label, x)
    counts = np.bincount(label, minlength=k)[:, None]
    # 点が来なかった塊は、中心をそのまま残す
    return np.where(counts > 0, sums / np.maximum(counts, 1), centers)


def medoids(x: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """k-means の各塊に、いちばん近い実在の点の番号。"""
    if len(x) <= k:
        return np.arange(len(x))
    centers = _seed_centers(x, k, seed)
    squared = (x**2).sum(1)
    doubled = 2 * x
    rows = np.arange(len(x))
    previous = None
    for _ in range(KMEANS_ITERATIONS):
        distances = _distances(doubled, squared, centers)
        label = distances.argmin(1)
        if previous is not None and np.array_equal(label, previous):
            break
        del distances
        centers = _update_centers(x, centers, label, rows)
        previous = label
    else:
        # 反復上限に達したときは、最後に更新した中心から距離を取り直す。
        distances = _distances(doubled, squared, centers)
    return np.unique(distances.argmin(0))


def summarize(states: list[np.ndarray]) -> tuple[np.ndarray, int]:
    """ブロックごとの表現を TOKENS 個に要約する。中心にいちばん近いブロックの番号も返す。"""
    firsts = np.stack([s[0] for s in states])
    lead = firsts[np.argmin(((firsts - firsts.mean(0)) ** 2).sum(1))]
    pool = np.concatenate([s[1:] for s in states])
    rest = pool[medoids(pool, TOKENS - 1)]
    means = np.stack([s.mean(0) for s in states])
    center = np.concatenate(states).mean(0)
    block = int(np.argmax(means @ center / np.linalg.norm(means, axis=1)))
    return np.concatenate([lead[None], rest]).astype(np.float32), block


async def fold(
    files: list[tuple[str, Path]],
    directory: Path,
    *,
    enhance: curate.Enhance,
    dereverb: curate.Dereverb | None,
    diarize: curate.Diarize,
    encode: EncodeSpeaker,
    model: str,
    offload: curate.Offload,
    progress: Progress,
) -> Folded:
    """素材を畳み込む。directory に作業用の音を置く。model は encode が使う合成モデルの名前。"""
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    progress("decode", 0.0)
    material = await offload(curate.decode, files, raw_dir)
    progress("diarize", 0.0)
    analysis = await curate.analyze(material, diarize, offload, rank_candidates=False)
    piece_count = len(analysis.pieces)
    pieces = await clean_pieces(analysis, enhance, offload, progress)
    del analysis, material
    await offload(shutil.rmtree, raw_dir)
    if dereverb is not None and pieces:
        pieces = await remove_reverb(pieces, dereverb, offload, progress)
    blocks = await offload(speech_blocks, pieces)
    if not blocks:
        raise curate.TooLittleSpeech("本人の声が短すぎます")
    states = []
    for i, block in enumerate(blocks):
        progress("encode", i / len(blocks))
        states.append(await encode(block))
    progress("summarize", 0.0)
    tokens, center = await offload(summarize, states)
    report = {
        "speech_seconds": round(sum(len(b) for b in blocks) / RATE, 1),
        "pieces": piece_count,
        "blocks": len(blocks),
        "pooled_tokens": int(sum(len(s) for s in states)),
        "tokens": len(tokens),
    }
    return Folded(tokens, blocks[center], report, model)


# 終わった仕事の結果を、問い合わせに答えるために残しておく数
JOBS_KEPT = 10


@dataclass
class FoldJob:
    job_id: str
    status: str = "running"
    stage: str = "upload"
    progress: float = 1.0
    profile: dict | None = None
    report: dict | None = None
    error: str | None = None
    detail: str | None = None

    def advance(self, stage: str, fraction: float) -> None:
        self.stage, self.progress = stage, round(min(max(fraction, 0.0), 1.0), 3)


class FoldJobs:
    """後ろで走らせる畳み込みの仕事。数分かかる（数人が話す素材 77 分で 3 分半。本人の声が多いほど延びる）ので、受け付けたら返して問い合わせてもらう。"""

    def __init__(self) -> None:
        self._jobs: dict[str, FoldJob] = {}
        # 走らせている task を握っておく（捨てると GC で止まる）。止めるときにも使う
        self._tasks: dict[str, asyncio.Task] = {}

    def start(
        self,
        work: Callable[[Progress], Awaitable[tuple[dict, dict]]],
        on_error: Callable[[Exception], tuple[str, str | None]],
    ) -> FoldJob:
        """work は進み具合を受けて (作った声, 報告) を返す。失敗は on_error でコードと説明にする。"""
        finished = [k for k, j in self._jobs.items() if j.status != "running"]
        for old in finished[: max(0, len(self._jobs) + 1 - JOBS_KEPT)]:
            del self._jobs[old]
        job = FoldJob(secrets.token_hex(8))
        self._jobs[job.job_id] = job

        async def run() -> None:
            try:
                job.profile, job.report = await work(job.advance)
                job.status, job.progress = "done", 1.0
            except Exception as error:
                _log.exception("fold job %s failed", job.job_id)
                job.error, job.detail = on_error(error)
                job.status = "failed"

        task = asyncio.create_task(run())
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))
        return job

    def get(self, job_id: str) -> FoldJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise FoldJobNotFound(job_id)
        return job

    async def stop(self, job_id: str) -> None:
        """走っていれば止めて、終わるまで待ってから仕事を消す。終わった仕事は記録だけを消す。

        保存まで進んでいたら止めずに保存を終えさせる（消えた仕事の声が途中までできるのを避ける）。
        列に並べた処理は待たずに抜けるが、MLX のスレッドが手を付けた1つ（数秒）と、
        別のスレッドで走っているデコードは最後まで走る。
        """
        job = self.get(job_id)
        task = self._tasks.get(job_id)
        if task is not None:
            if job.stage != "save":
                task.cancel()
            await asyncio.wait({task})
        self._jobs.pop(job_id, None)
