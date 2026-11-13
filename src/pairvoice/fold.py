"""長い素材の声を、話者の表現（トークンの列）1つに畳み込む。

30 秒の参照音声は、どの区間を選ぶかで声がぶれ、人が聴いて選ぶしかなかった。素材の
本人の声を全部トークンにして要約すれば、選ばずに素材全体の平均の声が得られる。

1. 素材を丸ごと、参照音声と同じクリーンアップにかける（雑音除去 → 残響除去）
2. きれいにした音で話者を分け、いちばん多く話す人の区間を全部使う（S/N や話し方では選ばない）
3. 区間の長い無音を詰めてつなぎ、BLOCK_SECONDS ずつ話者の表現にする
4. 溜めたトークンを TOKENS 個に要約する

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
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import curate

_log = logging.getLogger(__name__)

RATE = curate.OUTPUT_RATE
# 要約したトークンの数。モデルの勧める参照音声の長さ（30 秒前後）のトークン数にそろえる
TOKENS = 180
# 話者の表現にする1回の長さ（ふだんの参照音声と同じくらい）
BLOCK_SECONDS = 26.0
# 雑音除去を1回にかける長さ。前後に curate.ENHANCE_CONTEXT_SECONDS の文脈を足す
ENHANCE_SECONDS = 20.0
# クリーンアップを1回に通す長さと、前後に足す文脈（残響除去の窓より長く）。数時間のファイルを
# 丸ごと抱えると、残響除去の途中の配列で数 GB になる
SPAN_SECONDS = 300.0
SPAN_CONTEXT_SECONDS = 10.0
# 区間の中で、いちばん大きい所からこれより小さい 10ms は無音とみなす
SILENCE_DB = 40.0
# これより長い無音は、この長さに詰める
MAX_PAUSE_SECONDS = 0.3
KMEANS_ITERATIONS = 30


class FoldJobNotFound(Exception):
    """畳み込みの仕事が無い（ID の取り違え・常駐サーバーを再起動した）。"""


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


async def _clean_span(
    x: np.ndarray,
    enhance: curate.Enhance,
    dereverb: curate.Dereverb | None,
    offload: curate.Offload,
) -> np.ndarray:
    step = int(ENHANCE_SECONDS * RATE)
    context = int(curate.ENHANCE_CONTEXT_SECONDS * RATE)
    y = np.zeros_like(x)
    for start in range(0, len(x), step):
        lo, hi = max(0, start - context), min(len(x), start + step + context)
        end = min(len(x), start + step)
        y[start:end] = (await enhance(x[lo:hi]))[start - lo : end - lo]
    return y if dereverb is None else await curate.remove_reverb(y, dereverb, offload)


async def clean(
    material: curate.Material,
    directory: Path,
    enhance: curate.Enhance,
    dereverb: curate.Dereverb | None,
    offload: curate.Offload,
    progress: Progress,
) -> list[tuple[str, Path]]:
    """ファイルごとに丸ごと雑音と響きを除き、48kHz モノラルの wav に書く。

    SPAN_SECONDS ずつ前後の文脈つきで除いて書き足す（長い素材をメモリに抱えない）。
    """
    span, context = int(SPAN_SECONDS * RATE), int(SPAN_CONTEXT_SECONDS * RATE)
    total = sum(s.seconds for s in material.sources)
    done = 0.0
    cleaned = []
    for source in material.sources:
        offset, length = int(source.offset * RATE), int(source.seconds * RATE)
        path = directory / f"{len(cleaned)}.wav"
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(RATE)
            for start in range(0, length, span):
                lo, hi = max(0, start - context), min(length, start + span + context)
                x = curate.louder_channel(
                    np.asarray(material.audio48[offset + lo : offset + hi], np.float32)
                )
                y = await _clean_span(x, enhance, dereverb, offload)
                end = min(length, start + span)
                pcm = (np.clip(y[start - lo : end - lo], -1.0, 1.0) * 32767.0).astype("<i2")
                writer.writeframes(pcm.tobytes())
                progress("clean", (done + end / RATE) / total)
        done += source.seconds
        cleaned.append((source.name, path))
    return cleaned


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


def speech_blocks(analysis: curate.Analysis) -> list[np.ndarray]:
    """話者分離で取れた本人の区間を全部、無音を詰めてつなぎ、BLOCK_SECONDS ずつに切る。

    区間ごとの声の大きさは、ふだんの参照音声と同じくそろえる（素材の録り方で十数 dB 違い、
    小さいまま符号化すると、モデルがふだん見ている参照音声と大きさの違う声を表現にする）。
    """
    audio = analysis.material.audio48
    parts = []
    for piece in analysis.pieces:
        a, b = int(piece.start * RATE), int(piece.end * RATE)
        trimmed = trim_silence(curate.louder_channel(np.asarray(audio[a:b], np.float32)))
        if len(trimmed):
            parts.append(curate.level_piece(trimmed))
    speech = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    step = int(BLOCK_SECONDS * RATE)
    # 端数が半分に満たなければ捨てる（短すぎる表現は、ほかのブロックと性質が違う）
    return [
        curate.limit_peaks(speech[i : i + step], RATE)
        for i in range(0, len(speech) - step // 2, step)
    ]


def _distances(x: np.ndarray, squared: np.ndarray, centers: np.ndarray) -> np.ndarray:
    return squared[:, None] - 2 * x @ centers.T + (centers**2).sum(1)[None]


def medoids(x: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """k-means（k-means++ で始める）の各塊に、いちばん近い実在の点の番号。"""
    if len(x) <= k:
        return np.arange(len(x))
    rng = np.random.default_rng(seed)
    centers = [x[rng.integers(len(x))]]
    nearest = ((x - centers[0]) ** 2).sum(1)
    for _ in range(k - 1):
        centers.append(x[rng.choice(len(x), p=nearest / nearest.sum())])
        nearest = np.minimum(nearest, ((x - centers[-1]) ** 2).sum(1))
    c = np.stack(centers)
    squared = (x**2).sum(1)
    for _ in range(KMEANS_ITERATIONS):
        label = _distances(x, squared, c).argmin(1)
        sums = np.zeros_like(c)
        np.add.at(sums, label, x)
        counts = np.bincount(label, minlength=k)[:, None]
        # 点が来なかった塊は、中心をそのまま残す
        c = np.where(counts > 0, sums / np.maximum(counts, 1), c)
    return np.unique(_distances(x, squared, c).argmin(0))


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
    for sub in ("raw", "clean", "decoded"):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    progress("decode", 0.0)
    raw = await offload(curate.decode, files, directory / "raw")
    paths = await clean(raw, directory / "clean", enhance, dereverb, offload, progress)
    del raw
    # デコードした元の音はもう読まない（数時間分で数 GB になる）
    await offload(shutil.rmtree, directory / "raw")
    progress("diarize", 0.0)
    material = await offload(curate.decode, paths, directory / "decoded")
    analysis = await curate.analyze(material, diarize, offload)
    blocks = await offload(speech_blocks, analysis)
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
        "pieces": len(analysis.pieces),
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
    """後ろで走らせる畳み込みの仕事。数十分かかるので、受け付けたら返して問い合わせてもらう。"""

    def __init__(self) -> None:
        self._jobs: dict[str, FoldJob] = {}
        # 走らせている task を握っておく（捨てると GC で止まる）
        self._tasks: set[asyncio.Task] = set()

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
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def get(self, job_id: str) -> FoldJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise FoldJobNotFound(job_id)
        return job
