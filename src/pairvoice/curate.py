"""雑多な音声ファイルから参照音声を作る。

長い・形式がばらばら・BGM や雑音が乗った素材から、いちばん多く話す人の声のきれいな
区間を選び、雑音を除いてつなぐ。

1. ffmpeg で 16kHz のモノラルと 48kHz のステレオにデコードし、ファイルの間に無音を挟んで
   1本の流れにする（一時ファイルに書き、memmap で読む。1時間の素材でもメモリに載せきらない）
2. Sortformer で話者を分ける。逐次処理は話者の記憶を持ち越すので、ファイルをまたいでも
   同じ人に同じ番号が付く
3. いちばん多く話す人だけが話している区間を切り出し、話していない隙間の音と比べて採点する。
   同じ人の発話は、間が短くほかの人を挟まなければ1つの区間にし、息継ぎの間も残す
4. 区間ごとに声の大きさ・高さ・明るさを測り、その人のふだんの声に近い順に並べる（analyze）
5. 並べた順か、利用者が選んだ区間から MossFormer2 で雑音を除き、除いた量でも採点する
6. 雑音を除いた音で、区間の頭と末尾を近くの静かな所（話の切れ目）へずらす。話者分離の
   境目は粗く、そのまま切ると言葉の途中から始まり、途中で終わる
7. TARGET_SECONDS ぶん集め、雑音を除いた音をつなぐ（render）

S/N だけで選ぶと、張り上げた声（怒鳴る・叫ぶ）ほど背景音との差が開いて点が上がり、
参照音声が強い感情の声に偏る。参照音声の話し方は合成した声に移るので、S/N は使えるかの
足切りにだけ使い、順位はふだんの声との近さで決める。感情認識のモデル（SenseVoiceSmall）も
試したが、アニメ調の演技の区間はほぼすべて unk になり、使えなかった。

参照音声に残った BGM や雑音は、Irodori-TTS が声と一緒に真似る（合成した声にも乗る）。
きれいに聞こえる素材にも小さな効果音や雑音がうっすら乗るので、どの区間も雑音を除く。
除去のモデルは、きれいな声と足したノイズで測って選んだ。DeepFilterNet は音楽の BGM を
ほとんど消せず（S/N 10 dB の音楽で SI-SDR 10 → 11.5）、除いた声もこもって聞こえた。
MossFormer2 は 20.4 まで戻し、ノイズの無い声も崩しにくい（同 26.0 と 32.8）。
除去は和音のような BGM を消しきれず、消せなかった分は除いた量に映らないので、隙間の音でも測る。

ステレオの素材は左右を混ぜず、区間ごとに声の大きい方のチャンネルを使う。左右で位相の
違う成分（残響や広がり）が打ち消し合い、混ぜるとこもる。

MLX を触るのは diarize と enhance だけで、どちらも Engine が Runner の仕事として1回ずつ
回す（区切って回すので、長い素材でもその間にフックの読み上げが割り込める）。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from . import hub
from .config import CurateConfig
from .postprocess import PEAK_CEILING_DB, TARGET_RMS_DB, voiced_rms_db

DIARIZE_RATE = 16000
OUTPUT_RATE = 48000
# 48kHz のステレオのチャンネル数。モノラルの素材は ffmpeg が同じ音を2つに写す
OUTPUT_CHANNELS = 2
# ファイルの間に挟む無音。話者分離が別のファイルの発話を1つの区間につながないように
FILE_GAP_SECONDS = 1.0
# デコードする長さの上限。一時ファイルは 48kHz で1時間あたり約 0.7GB
MAX_TOTAL_SECONDS = 3 * 3600.0
# 話者分離を1回の仕事で進める長さと、その中で Sortformer に1回で渡す長さ。1回で長く渡すと
# 話者の記憶をまとめ直す処理が膨らみ、10 分の素材で数分かかる（10 秒ずつなら数秒）
DIARIZE_CHUNK_SECONDS = 60.0
FEED_SECONDS = 10.0
FRAME_SECONDS = 0.01
# 同じ人の発話の間がこれより短ければ1つの区間にする
MERGE_GAP_SECONDS = 0.3
# 同じ人の発話の間がこれより短く、間にほかの人の声が無ければ、1つの区間にする
TURN_GAP_SECONDS = 1.0
# 区間の長さ。短い区間は話し方が乗らず、つなぎ目が増えてぶつ切りに聞こえる
MIN_PIECE_SECONDS = 3.0
MAX_PIECE_SECONDS = 12.0
# 長い区間を切るとき、この長さ以降の、PAUSE_SECONDS の幅でならしていちばん静かな所で切る
# （1フレームだけ静かな所は、言葉の中の子音の閉じでも起きる）
SPLIT_FROM_SECONDS = 4.0
PAUSE_SECONDS = 0.2
# 区間の頭と末尾は、前後これだけの中で、声よりこれだけ静かな所までずらす。話者分離は
# 発話の頭を 0.5 秒近く遅れて付けることがある。ボイスドラマの間は声より 25〜30 dB 下で、
# 30 dB にすると間がとぎれとぎれになって見つからなかった
EDGE_SEARCH_SECONDS = 1.0
QUIET_DB = 25.0
# 静かな所が見つかったら、声からこれだけ離れるまで静かな所の中へ寄せて切る。声の際で切ると、
# つなぐときのフェードが言葉の頭や末尾にかかり、m や n のような弱い子音が消える
EDGE_PAD_SECONDS = 0.1
# これより長く静かなら、話の間とみなして優先する。短い静けさは言葉の中にもある（破裂音の
# 閉じや促音）ので、そこで切ると言葉の途中から始まる
PAUSE_MIN_SECONDS = 0.15
# 参照音声の長さ。TARGET_SECONDS に届くまで、MAX_REFERENCE_SECONDS を超えない区間を足す。
# 区間は話の切れ目で切るので長さがまちまちで、ちょうど TARGET_SECONDS には詰まらない。
# Irodori-TTS は 30 秒までを使う
TARGET_SECONDS = 20.0
MAX_REFERENCE_SECONDS = 28.0
# これより短くしか集まらなければ作らない
MIN_TOTAL_SECONDS = 6.0
# これより点の低い区間は自動では使わない。明らかに使えない区間（BGM が声と同じくらい
# 大きいなど）を除くための安全柵。いちばん良い区間との差で切ると、点の高い区間が1つ
# あるだけで、ほかの区間がまとめて切られた（点は隙間の音と除いた量から推した目安で、
# 差の大きさに決まった意味は無い）
MIN_SNR_DB = 8.0
# 自動で選ぶときは、点がこれ以上の区間を先に並べる。これより下は雑音が耳につき始める
GOOD_SNR_DB = 15.0
# 自動で選ぶとき、雑音を除くのは合わせてこの長さまで（雑音の多い長い素材で、除いては
# 捨てる区間が続いても、仕事の数とメモリを抑える）
ENHANCE_MAX_SECONDS = 300.0
# 話し方を測る窓（16kHz で 40ms）。F0 の下限の周期を2つ以上含む長さ
VOICE_WINDOW = 640
PITCH_MIN_HZ = 70.0
PITCH_MAX_HZ = 800.0
# 正規化した自己相関のピークがこれより高い窓を、高さを測れる窓とみなす
VOICING = 0.5
# 区間でいちばん大きい窓からこれ以内の窓だけを測る（息継ぎや語尾の消え際を除く）
VOICED_RANGE_DB = 25.0
MIN_PITCHED_FRAMES = 10
# ふだんの声からの離れを測るとき、ばらつき（MAD）の下限。区間がそろいすぎていると、
# わずかな差が大きな離れに見える。高さと明るさは半音、大きさは dB
SPREAD_FLOOR = {"loudness": 1.5, "pitch": 1.0, "brightness": 1.0}
# 高さを測れなかった区間の、高さの離れ（ふつうより少し外れたものとして扱う）
UNPITCHED_DEVIATION = 1.5
# 選んだ区間を聴き比べるときに一度に返す長さの上限
EXCERPT_MAX_SECONDS = 60.0
# 素材全体の音の大きさの波形を返すときの点の数の上限（10ms ごと、超えれば間引く）
ENVELOPE_MAX_POINTS = 20000
ENVELOPE_FLOOR_DB = -60.0
# 背景音は、区間の前後これだけの中の、誰も話していない所で測る
BACKGROUND_WINDOW_SECONDS = 10.0
# 声の端が背景音に混ざらないよう、話している所の前後これだけも声とみなす
BACKGROUND_GUARD_SECONDS = 0.1
MIN_BACKGROUND_SECONDS = 0.3
# 雑音を除くときに前後に付ける文脈（除去のモデルは頭の数百ミリ秒で雑音をつかむ）と、つなぐときに残す余白
ENHANCE_CONTEXT_SECONDS = 2.0
PIECE_GAP_SECONDS = 0.3
FADE_SECONDS = 0.03
# 区間ごとに声の大きさを TARGET_RMS_DB にそろえるとき、持ち上げる量の上限（雑音を除いた
# 後なので、小さな区間を大きく持ち上げても雑音は目立たない）
PIECE_MAX_GAIN_DB = 24.0
# ピークを抑えるリミッターの、音量を下げてから戻すまでの時間
LIMIT_BLOCK_SECONDS = 0.005
LIMIT_RELEASE_SECONDS = 0.1
# これより大きいサンプルが CLIP_RATIO を超える区間は、音が割れているとみなす
CLIP_LEVEL = 0.99
CLIP_RATIO = 0.001


class CurateError(Exception):
    """素材から参照音声を作れない。API の応答は api_errors が種類ごとに決める。"""


class InvalidAudio(CurateError):
    """読めないファイルがある。文言に、どのファイルかを入れる。"""


class TooLittleSpeech(CurateError):
    """きれいな声が足りない。"""


class FfmpegMissing(CurateError):
    """素材を読む ffmpeg が無い。"""


class InvalidSelection(CurateError):
    """選んだ区間が使えない（番号が無い・長さが足りない・長すぎる）。"""


@dataclass(frozen=True)
class Segment:
    """話者分離の1区間（流れの先頭からの秒）。"""

    start: float
    end: float
    speaker: int


@dataclass(frozen=True)
class Source:
    name: str
    # 流れの中での始まりと長さ（秒）
    offset: float
    seconds: float


@dataclass
class Material:
    """デコードした素材。audio16（モノラル）は話者分離と隙間の音、audio48（フレーム × チャンネル）は参照音声に使う。"""

    audio16: np.ndarray
    audio48: np.ndarray
    sources: list[Source]
    truncated: bool = False

    @property
    def seconds(self) -> float:
        return len(self.audio16) / DIARIZE_RATE

    def chunks(self, seconds: float = DIARIZE_CHUNK_SECONDS) -> Iterator[np.ndarray]:
        step = int(seconds * DIARIZE_RATE)
        for start in range(0, len(self.audio16), step):
            yield np.array(self.audio16[start : start + step], dtype=np.float32)

    def source_at(self, at: float) -> Source:
        return next(s for s in reversed(self.sources) if s.offset <= at)

    def gap_frames(self) -> np.ndarray:
        """ファイルの間に挟んだ無音のフレーム。背景音の測り方を狂わせるので除く。"""
        frames = len(self.audio16) // int(DIARIZE_RATE * FRAME_SECONDS)
        gaps = np.zeros(frames, bool)
        for s in self.sources:
            end = int((s.offset + s.seconds) / FRAME_SECONDS)
            gaps[end : end + int(FILE_GAP_SECONDS / FRAME_SECONDS)] = True
        return gaps


@dataclass(frozen=True)
class Voice:
    """区間の話し方。大きさは dBFS、高さは F0 の中央値、明るさはスペクトルの重心。"""

    loudness_db: float
    pitch_hz: float | None
    brightness_hz: float


@dataclass
class Piece:
    start: float
    end: float
    background_snr: float
    # 頭と末尾をずらしてよい範囲（ほかの人の声とファイルの継ぎ目の手前まで）
    min_start: float = 0.0
    max_end: float = float("inf")
    voice: Voice | None = None
    # analyze が付ける、流れの順の番号（API の候補の index）
    index: int = 0
    # ふだんの声からの離れ（各特徴の外れ方の二乗平均の平方根）。小さいほどふだんの声
    deviation: float | None = None
    enhanced_snr: float | None = None
    # 参照音声に使う、雑音を除いた音。雑音を除いて測るまでは None
    audio: np.ndarray | None = field(default=None, repr=False)

    @property
    def seconds(self) -> float:
        return self.end - self.start

    @property
    def score(self) -> float:
        if self.enhanced_snr is None:
            return self.background_snr
        return min(self.background_snr, self.enhanced_snr)


@dataclass(frozen=True)
class Outcome:
    samples: np.ndarray
    sample_rate: int
    report: dict


@dataclass
class Analysis:
    """話者分離と区間の採点まで。素材と合わせて持てば、選び方を変えて何度でも組み立てられる。

    pieces は流れの順、ranked は自動で選ぶときに雑音を除いて試す順（区間の番号）。
    """

    material: Material
    # 10ms ごとの音の大きさ（frame_power）。タイムラインの波形にも使う
    power: np.ndarray
    segments: list[Segment]
    talk: dict[int, float]
    speaker: int
    pieces: list[Piece]
    ranked: list[int]
    # 候補のセリフの聞き取り（区間の番号 → 文を返す仕事）。一度頼んだものは使い回し、
    # 聞き取りの最中に同じ候補を頼まれても、同じ仕事の答えを待つ
    transcripts: dict[int, asyncio.Future[str]] = field(default_factory=dict)


def decode(files: list[tuple[str, Path]], directory: Path) -> Material:
    """(名前, パス) の素材を、無音を挟んだ1本の流れにデコードする。読めないファイルは InvalidAudio。"""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FfmpegMissing("ffmpeg が要ります（brew install ffmpeg）")
    rates = (DIARIZE_RATE, OUTPUT_RATE)
    channels = {DIARIZE_RATE: 1, OUTPUT_RATE: OUTPUT_CHANNELS}
    streams = {rate: (directory / f"audio{rate}.f32").open("wb") for rate in rates}
    sources: list[Source] = []
    offset, truncated = 0.0, False
    try:
        for name, path in files:
            left = MAX_TOTAL_SECONDS - offset
            if left < MIN_PIECE_SECONDS:
                truncated = True
                break
            # 1回のデコードから2つの rate に書き分ける。動画は音声だけを、複数の音声トラックは
            # 最初のものを読む
            parts = {rate: directory / f"part{rate}.f32" for rate in rates}
            outputs = [
                arg
                for rate, part in parts.items()
                for arg in (
                    *("-map", "0:a:0", "-t", f"{left:.3f}", "-ac", str(channels[rate])),
                    *("-ar", str(rate)),
                    *("-f", "f32le", "-y", str(part)),
                )
            ]
            done = subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-i", str(path), *outputs],
                stderr=subprocess.PIPE,
                check=False,
            )
            if done.returncode != 0:
                detail = done.stderr.decode(errors="replace").strip().splitlines()
                raise InvalidAudio(f"{name}: {detail[-1] if detail else '読めません'}")
            seconds = min(
                part.stat().st_size // (4 * channels[rate]) / rate for rate, part in parts.items()
            )
            if seconds <= 0:
                raise InvalidAudio(f"{name}: 音声がありません")
            # -t で打ち切った
            truncated = truncated or seconds >= left - 0.01
            sources.append(Source(name=name, offset=offset, seconds=seconds))
            # 2つの rate の長さを揃えて足し、次のファイルとの間に無音を挟む
            for rate, part in parts.items():
                with part.open("rb") as source:
                    _copy(source, streams[rate], round(seconds * rate) * 4 * channels[rate])
                streams[rate].write(bytes(round(FILE_GAP_SECONDS * rate) * 4 * channels[rate]))
                part.unlink()
            offset += seconds + FILE_GAP_SECONDS
    finally:
        for stream in streams.values():
            stream.close()
    audio = {
        rate: np.memmap(stream.name, dtype="<f4", mode="r") for rate, stream in streams.items()
    }
    audio48 = audio[OUTPUT_RATE].reshape(-1, OUTPUT_CHANNELS)
    return Material(audio[DIARIZE_RATE], audio48, sources, truncated)


def _copy(source, sink, size: int) -> None:
    while size > 0 and (block := source.read(min(size, 1 << 20))):
        sink.write(block)
        size -= len(block)


def merge_segments(segments: list[Segment]) -> list[Segment]:
    """同じ人の、間の短い区間をつなぐ（話者分離が仕事の区切りで割った区間も戻る）。"""
    merged: list[Segment] = []
    for segment in sorted(segments, key=lambda s: (s.speaker, s.start)):
        last = merged[-1] if merged else None
        if (
            last
            and last.speaker == segment.speaker
            and segment.start - last.end < MERGE_GAP_SECONDS
        ):
            merged[-1] = Segment(last.start, max(last.end, segment.end), last.speaker)
        else:
            merged.append(segment)
    return sorted(merged, key=lambda s: s.start)


def talk_seconds(segments: list[Segment]) -> dict[int, float]:
    talk: dict[int, float] = {}
    for s in segments:
        talk[s.speaker] = talk.get(s.speaker, 0.0) + s.end - s.start
    return talk


def frame_power(audio16: np.ndarray) -> np.ndarray:
    hop = int(DIARIZE_RATE * FRAME_SECONDS)
    frames = len(audio16) // hop
    # memmap を一度に読むと 1 時間で 0.2GB を作るので、区切って求める
    power = np.empty(frames, dtype=np.float64)
    step = 360000
    for start in range(0, frames, step):
        block = np.asarray(audio16[start * hop : min(frames, start + step) * hop], np.float64)
        power[start : start + step] = (block.reshape(-1, hop) ** 2).mean(axis=1)
    return power


def find_pieces(
    segments: list[Segment], speaker: int, power: np.ndarray, excluded: np.ndarray
) -> list[Piece]:
    """その人だけが話している区間を切り出し、隙間の音と比べた点を付ける。excluded は背景音に数えない。"""
    frames = len(power)
    own = np.zeros(frames, bool)
    others = np.zeros(frames, bool)
    for s in segments:
        a, b = int(s.start / FRAME_SECONDS), int(np.ceil(s.end / FRAME_SECONDS))
        (own if s.speaker == speaker else others)[a:b] = True
    guard = int(BACKGROUND_GUARD_SECONDS / FRAME_SECONDS)
    speech = _dilate(own | others, guard) | excluded[:frames]
    background = power[~speech]
    fallback = float(np.median(background)) if len(background) else None

    blocked = others | excluded[:frames]
    search = int(EDGE_SEARCH_SECONDS / FRAME_SECONDS)
    pieces = []
    for a, b in _runs(own & ~others, int(TURN_GAP_SECONDS / FRAME_SECONDS), blocked):
        before = np.flatnonzero(blocked[max(0, a - search) : a])
        after = np.flatnonzero(blocked[b : b + search])
        min_start = (a - search + before[-1] + 1 if len(before) else a - search) * FRAME_SECONDS
        max_end = (b + after[0] if len(after) else b + search) * FRAME_SECONDS
        for a2, b2 in _split(a, b, power):
            if (b2 - a2) * FRAME_SECONDS < MIN_PIECE_SECONDS:
                continue
            window = int(BACKGROUND_WINDOW_SECONDS / FRAME_SECONDS)
            lo, hi = max(0, a2 - window), min(frames, b2 + window)
            near = power[lo:hi][~speech[lo:hi]]
            floor = (
                float(np.median(near))
                if len(near) * FRAME_SECONDS >= MIN_BACKGROUND_SECONDS
                else fallback
            )
            voice = float(power[a2:b2].mean())
            # 隙間が無い素材は背景音を測れないので、雑音を除いた量だけで決める。
            # 背景がデジタルの無音なら、背景音は無い
            snr = np.inf if not floor else 10 * np.log10(max(voice / floor, 1e-12))
            piece = Piece(a2 * FRAME_SECONDS, b2 * FRAME_SECONDS, float(snr))
            # 切った所は話の途中なので、隣の区間の側へもずらしてよい
            piece.min_start = max(0.0, min_start if a2 == a else (a2 - search) * FRAME_SECONDS)
            piece.max_end = max_end if b2 == b else (b2 + search) * FRAME_SECONDS
            pieces.append(piece)
    return pieces


def _edges(mask: np.ndarray) -> np.ndarray:
    """mask の True の連なりの (始まり, 終わり) の組。"""
    return np.flatnonzero(np.diff(np.r_[0, mask.astype(np.int8), 0])).reshape(-1, 2)


def _dilate(mask: np.ndarray, width: int) -> np.ndarray:
    if width <= 0 or not mask.any():
        return mask
    edges = _edges(mask)
    out = mask.copy()
    for a, b in edges:
        out[max(0, a - width) : b + width] = True
    return out


def _runs(mask: np.ndarray, merge: int, blocked: np.ndarray) -> list[tuple[int, int]]:
    """mask の True の連なり。間が merge 未満で、間にほかの人の声が無ければつなぐ。"""
    edges = _edges(mask)
    runs: list[list[int]] = []
    for a, b in edges.tolist():
        if runs and a - runs[-1][1] < merge and not blocked[runs[-1][1] : a].any():
            runs[-1][1] = b
        else:
            runs.append([a, b])
    return [(a, b) for a, b in runs]


def _split(a: int, b: int, power: np.ndarray) -> list[tuple[int, int]]:
    """MAX_PIECE_SECONDS を超える区間を、SPLIT_FROM_SECONDS 以降のいちばん静かな間で切る。"""
    longest = int(MAX_PIECE_SECONDS / FRAME_SECONDS)
    earliest = int(SPLIT_FROM_SECONDS / FRAME_SECONDS)
    width = int(PAUSE_SECONDS / FRAME_SECONDS)
    out = []
    while b - a > longest:
        span = power[a + earliest : a + longest]
        smooth = _smooth(span, width)
        cut = a + earliest + int(np.argmin(smooth))
        out.append((a, cut))
        a = cut
    out.append((a, b))
    return out


def snap_edges(piece: Piece, audio: np.ndarray, offset: float) -> tuple[float, float]:
    """区間の頭と末尾を、前後 EDGE_SEARCH_SECONDS の中のいちばん近い静かな所へずらす。

    audio は offset 秒から始まる音。PAUSE_MIN_SECONDS 以上続く静かな所（話の間）を先に探し、
    無ければ短い静かな所で切る。静かな所の中では、声から EDGE_PAD_SECONDS 離れるまで外側へ
    寄せる。声より QUIET_DB 静かな所が無ければ、探した範囲でいちばん静かな所で切る
    """
    hop = int(FRAME_SECONDS * OUTPUT_RATE)
    frames = len(audio) // hop
    # 2フレームの幅で大きさを測る（1フレームでは子音の閉じを間と取り違える）
    power = (audio[: frames * hop].reshape(frames, hop).astype(np.float64) ** 2).mean(axis=1)
    level = 10 * np.log10(_smooth(power, 2) + 1e-12)

    def at(t: float) -> int:
        return min(frames - 1, max(0, round((t - offset) / FRAME_SECONDS)))

    quiet = level < np.percentile(level[at(piece.start) : at(piece.end) + 1], 90) - QUIET_DB
    pause = _long_runs(quiet, round(PAUSE_MIN_SECONDS / FRAME_SECONDS))
    pad = round(EDGE_PAD_SECONDS / FRAME_SECONDS)

    def nearest(t: float, lo: float, hi: float, outward: int) -> float:
        """outward は区間の外側の向き（頭は -1、末尾は +1）。"""
        a, b = at(max(lo, t - EDGE_SEARCH_SECONDS)), at(min(hi, t + EDGE_SEARCH_SECONDS))
        if a >= b:
            return t
        candidates = np.arange(a, b + 1)
        mask = next((m for m in (pause, quiet) if m[a : b + 1].any()), None)
        if mask is None:
            best = candidates[np.argmin(level[a : b + 1])]
            return offset + float(best) * FRAME_SECONDS
        calm = candidates[mask[a : b + 1]]
        # 外側の静かな所を先に選ぶ（区間を広げる）。内側へ縮めると、話者分離が遅れて付けた
        # 頭や早く切った末尾で、言葉の途中から始まる・途中で終わる
        outside = calm[(calm - at(t)) * outward >= 0]
        calm = outside if len(outside) else calm
        best = int(calm[np.argmin(np.abs(calm - at(t)))])
        # 声の際（内側で最初に静かでなくなる所）から pad 離れた所を目指す。際から測るので、
        # 一度ずらした区間をもう一度ずらしても動かない
        edge = best
        while 0 <= edge - outward < frames and mask[edge - outward]:
            edge -= outward
        target = edge + outward * pad
        # 外側の限り（ほかの人の声やファイルの継ぎ目の手前）は探した範囲に関わらず守る
        limit = at(lo) if outward < 0 else at(hi)
        while (target - best) * outward > 0 and (limit - best) * outward > 0:
            if not mask[best + outward]:
                break
            best += outward
        return offset + float(best) * FRAME_SECONDS

    return (
        nearest(piece.start, piece.min_start, piece.end, -1),
        nearest(piece.end, piece.start, piece.max_end, +1),
    )


def _long_runs(mask: np.ndarray, length: int) -> np.ndarray:
    """mask の True が length 以上続く所だけを残す。"""
    edges = _edges(mask)
    out = np.zeros_like(mask)
    for a, b in edges:
        if b - a >= length:
            out[a:b] = True
    return out


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    """width フレームの移動平均。端はゼロでなく端の値で埋める（ゼロだと端が静かに見える）。"""
    padded = np.pad(values, (width // 2, width - 1 - width // 2), mode="edge")
    return np.convolve(padded, np.ones(width) / width, mode="valid")


def clipped(samples: np.ndarray) -> bool:
    return bool(np.mean(np.abs(samples) >= CLIP_LEVEL) > CLIP_RATIO)


def _louder_channel(frames: np.ndarray) -> np.ndarray:
    """フレーム × チャンネルのうち、声の大きいチャンネル。左右を混ぜるとこもる。"""
    return frames[:, int(np.argmax((frames**2).sum(axis=0)))]


def residual_snr(original: np.ndarray, enhanced: np.ndarray) -> float:
    """雑音を除いた音と、除いた分の比（dB）。"""
    removed = float(np.sum((original - enhanced) ** 2))
    kept = float(np.sum(enhanced**2))
    return float(10 * np.log10(max(kept, 1e-12) / max(removed, 1e-12)))


def refine_edges(pieces: list[Piece], material: Material) -> None:
    """区間の頭と末尾を、元の音で話の切れ目へずらす。

    話者分離の境目は粗く、そのままでは言葉の途中から始まる。タイムラインで聴く区間と、
    参照音声に使う区間が同じ所から始まるよう、解析の時点でずらしておく（雑音を除いた後に
    もう一度ずらすのは、除いた音のほうが切れ目がはっきりするから）
    """
    for piece in pieces:
        a = max(0, int((piece.start - EDGE_SEARCH_SECONDS) * OUTPUT_RATE))
        b = min(len(material.audio48), int((piece.end + EDGE_SEARCH_SECONDS) * OUTPUT_RATE))
        audio = _louder_channel(np.asarray(material.audio48[a:b], dtype=np.float32))
        piece.start, piece.end = snap_edges(piece, audio, a / OUTPUT_RATE)


def measure_voice(samples: np.ndarray) -> Voice | None:
    """16kHz の区間の話し方。短すぎて測れなければ None。"""
    hop = int(DIARIZE_RATE * FRAME_SECONDS)
    count = (len(samples) - VOICE_WINDOW) // hop + 1
    if count < 1:
        return None
    index = np.arange(VOICE_WINDOW)[None, :] + hop * np.arange(count)[:, None]
    frames = np.asarray(samples, np.float64)[index]
    frames -= frames.mean(axis=1, keepdims=True)
    level = 10 * np.log10((frames**2).mean(axis=1) + 1e-12)
    loud = level > level.max() - VOICED_RANGE_DB
    # 2倍の長さで変換すると、パワースペクトルを戻した値がそのまま自己相関になる
    spectrum = np.abs(np.fft.rfft(frames * np.hanning(VOICE_WINDOW), n=2 * VOICE_WINDOW))
    correlation = np.fft.irfft(spectrum**2)[:, :VOICE_WINDOW]
    correlation /= correlation[:, :1] + 1e-12
    shortest, longest = int(DIARIZE_RATE / PITCH_MAX_HZ), int(DIARIZE_RATE / PITCH_MIN_HZ)
    lag = shortest + np.argmax(correlation[:, shortest:longest], axis=1)
    pitched = loud & (correlation[np.arange(count), lag] > VOICING)
    freqs = np.fft.rfftfreq(2 * VOICE_WINDOW, 1 / DIARIZE_RATE)
    centroid = (spectrum * freqs).sum(axis=1) / (spectrum.sum(axis=1) + 1e-12)
    return Voice(
        loudness_db=float(np.median(level[loud])),
        pitch_hz=(
            float(np.median(DIARIZE_RATE / lag[pitched]))
            if pitched.sum() >= MIN_PITCHED_FRAMES
            else None
        ),
        brightness_hz=float(np.median(centroid[loud])),
    )


def measure_pieces(pieces: list[Piece], audio16: np.ndarray) -> None:
    for piece in pieces:
        a, b = int(piece.start * DIARIZE_RATE), int(piece.end * DIARIZE_RATE)
        piece.voice = measure_voice(np.asarray(audio16[a:b], np.float32))


def score_deviation(pieces: list[Piece], material: Material) -> None:
    """S/N が足りる区間に、ふだんの声からの離れ（deviation）を付ける。

    大きさ・高さ・明るさのそれぞれで、区間どうしの中央値からの外れ方をばらつき（MAD）で
    割って測り、その二乗平均の平方根を離れにする。叫びや怒鳴りは、大きく・高く・明るく
    なるので外れる。大きさは録り方でファイルごとに違うので、同じファイルの区間の中央値からの
    差で比べる。高さと明るさは半音（対数）で比べる
    """
    usable = [
        (p, p.voice) for p in pieces if p.voice is not None and p.background_snr >= MIN_SNR_DB
    ]
    if not usable:
        return
    sources = [material.source_at(p.start).name for p, _ in usable]
    by_source: dict[str, list[float]] = {}
    for name, (_, voice) in zip(sources, usable, strict=True):
        by_source.setdefault(name, []).append(voice.loudness_db)
    source_level = {name: float(np.median(levels)) for name, levels in by_source.items()}
    features = {
        "loudness": [
            v.loudness_db - source_level[name] for name, (_, v) in zip(sources, usable, strict=True)
        ],
        "pitch": [12 * np.log2(v.pitch_hz) if v.pitch_hz else np.nan for _, v in usable],
        "brightness": [12 * np.log2(max(v.brightness_hz, 1.0)) for _, v in usable],
    }
    squared = []
    for name, values in features.items():
        x = np.array(values, np.float64)
        center = np.nanmedian(x) if np.isfinite(x).any() else 0.0
        spread = max(1.4826 * float(np.nanmedian(np.abs(x - center))), SPREAD_FLOOR[name])
        z = np.abs(x - center) / spread
        squared.append(np.where(np.isfinite(z), z, UNPITCHED_DEVIATION) ** 2)
    for (piece, _), d in zip(usable, np.sqrt(np.mean(squared, axis=0)), strict=True):
        piece.deviation = float(d)


def rank(pieces: list[Piece]) -> list[int]:
    """自動で試す順（区間の番号）。離れを測った区間を、S/N が十分なものから、ふだんの声に近い順に並べる。"""
    scored = [p for p in pieces if p.deviation is not None]
    scored.sort(key=lambda p: (p.background_snr < GOOD_SNR_DB, p.deviation))
    return [p.index for p in scored]


def preselect(analysis: Analysis) -> list[int]:
    """自動で選ぶ区間の見込み（区間の番号、流れの順）。

    雑音を除く前の点で選ぶので、render(selection=None) は除いた量を見て入れ替えることがある
    """
    chosen, total = [], 0.0
    for i in analysis.ranked:
        if total >= TARGET_SECONDS:
            break
        seconds = analysis.pieces[i].seconds
        if total + seconds <= MAX_REFERENCE_SECONDS:
            chosen.append(i)
            total += seconds
    return sorted(chosen)


def assemble(pieces: list[Piece]) -> np.ndarray:
    """区間ごとに声の大きさをそろえてつなぎ、ピークを抑える。

    素材の中の離れた区間は大きさがまちまちで（同じ人でも 12 dB 違うことがあった）、全体に
    1つの倍率をかけても参照音声の中で音量が上下する。全体の倍率をピークに合わせると、
    一瞬の大きな音（笑い声や破裂音）のせいで全体が小さくなるので、ピークはリミッターで抑える
    """
    gap = np.zeros(int(PIECE_GAP_SECONDS * OUTPUT_RATE), np.float32)
    fade = int(FADE_SECONDS * OUTPUT_RATE)
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    parts = []
    for piece in pieces:
        assert piece.audio is not None  # render が雑音を除いた写しだけを渡す
        body = piece.audio.astype(np.float32, copy=True)
        rms_db = voiced_rms_db(body, OUTPUT_RATE)
        if rms_db is not None:
            body *= np.float32(10 ** (min(TARGET_RMS_DB - rms_db, PIECE_MAX_GAIN_DB) / 20))
        body[:fade] *= ramp
        body[-fade:] *= ramp[::-1]
        parts += [body, gap]
    return limit_peaks(np.concatenate(parts[:-1]), OUTPUT_RATE)


def limit_peaks(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """PEAK_CEILING_DB を超える所だけ音量を下げる。下げ始めは前のブロックから、戻しはゆっくり。"""
    ceiling = 10 ** (PEAK_CEILING_DB / 20)
    block = max(1, int(LIMIT_BLOCK_SECONDS * sample_rate))
    blocks = -(-len(samples) // block)
    padded = np.pad(np.abs(samples), (0, blocks * block - len(samples)))
    peaks = padded.reshape(blocks, block).max(axis=1)
    need = np.minimum(1.0, ceiling / np.maximum(peaks, 1e-9))
    # 前後のブロックも同じだけ下げる（ブロックの間を直線でつなぐので、そうしないと大きな音の
    # 所で倍率が戻りかける）。下げた分は LIMIT_RELEASE_SECONDS かけて戻す
    need = np.minimum(need, np.minimum(np.r_[need[1:], 1.0], np.r_[1.0, need[:-1]]))
    release = np.exp(-block / (LIMIT_RELEASE_SECONDS * sample_rate))
    gain = np.empty(blocks)
    level = 1.0
    for i, target in enumerate(need):
        level = target if target < level else min(target, 1 - (1 - level) * release)
        gain[i] = level
    # ブロックの中は直線でつなぎ、最後に念のため上限で切る
    centers = (np.arange(blocks) + 0.5) * block
    smooth = np.interp(np.arange(len(samples)), centers, gain)
    return np.clip(samples * smooth.astype(np.float32), -ceiling, ceiling)


Diarize = Callable[[np.ndarray], Awaitable[list[Segment]]]
Enhance = Callable[[np.ndarray], Awaitable[np.ndarray]]
Offload = Callable[..., Awaitable]


async def analyze(material: Material, diarize: Diarize, offload: Offload) -> Analysis:
    """話者を分け、いちばん多く話す人の区間を切り出して採点する。"""
    segments = []
    for chunk in material.chunks():
        segments += await diarize(chunk)
    segments = merge_segments(segments)
    talk = talk_seconds(segments)
    if not talk:
        raise TooLittleSpeech("声が見つかりません")
    speaker = max(talk, key=lambda k: talk[k])

    power = await offload(frame_power, material.audio16)
    pieces = await offload(find_pieces, segments, speaker, power, material.gap_frames())
    await offload(refine_edges, pieces, material)
    for index, piece in enumerate(pieces):
        piece.index = index
    await offload(measure_pieces, pieces, material.audio16)
    score_deviation(pieces, material)
    return Analysis(material, power, segments, talk, speaker, pieces, rank(pieces))


async def render(
    analysis: Analysis, selection: list[int] | None, enhance: Enhance, offload: Offload
) -> Outcome:
    """区間の雑音を除いてつなぐ。selection は区間の番号で、None なら自動で選ぶ。

    diarize と enhance はモデルを、offload は重い numpy をスレッドで回す。analysis の区間は
    書き換えない（同じ解析から選び方を変えて何度でも作れるように）
    """
    if selection is None:
        chosen = await _render_auto(analysis, enhance)
    else:
        chosen = await _render_selected(analysis, selection, enhance)
    total = sum(p.seconds for p in chosen)
    if total < MIN_TOTAL_SECONDS:
        raise TooLittleSpeech(
            f"きれいな声が {total:.1f} 秒しか見つかりません（{MIN_TOTAL_SECONDS:.0f} 秒以上が要ります）",
        )
    chosen.sort(key=lambda p: p.start)
    samples = await offload(assemble, chosen)
    return Outcome(samples, OUTPUT_RATE, _report(analysis, chosen))


async def _render_auto(analysis: Analysis, enhance: Enhance) -> list[Piece]:
    chosen, total, spent = [], 0.0, 0.0
    for i in analysis.ranked:
        if total >= TARGET_SECONDS or spent >= ENHANCE_MAX_SECONDS:
            break
        source = analysis.pieces[i]
        # 文脈まで見ると、割れた区間の隣まで落とす
        if total + source.seconds > MAX_REFERENCE_SECONDS or clipped(
            _original(analysis.material, source)[0]
        ):
            continue
        piece = await _enhance_piece(analysis.material, source, enhance)
        spent += piece.seconds
        if piece.seconds < MIN_PIECE_SECONDS or piece.score < MIN_SNR_DB:
            continue
        if total + piece.seconds > MAX_REFERENCE_SECONDS:
            continue
        chosen.append(piece)
        total += piece.seconds
    return chosen


async def _render_selected(
    analysis: Analysis, selection: list[int], enhance: Enhance
) -> list[Piece]:
    if len(set(selection)) != len(selection):
        raise InvalidSelection("同じ区間を2度選んでいます")
    if any(not 0 <= i < len(analysis.pieces) for i in selection):
        raise InvalidSelection(f"区間の番号は 0〜{len(analysis.pieces) - 1} です")
    total = sum(analysis.pieces[i].seconds for i in selection)
    if total > MAX_REFERENCE_SECONDS:
        raise InvalidSelection(
            f"選んだ区間が {total:.1f} 秒あります（{MAX_REFERENCE_SECONDS:.0f} 秒まで）"
        )
    if total < MIN_TOTAL_SECONDS:
        raise InvalidSelection(
            f"選んだ区間が {total:.1f} 秒しかありません（{MIN_TOTAL_SECONDS:.0f} 秒以上が要ります）"
        )
    # 選んだ人が聴いて決めたので、音割れも点の低さも理由に落とさない
    return [await _enhance_piece(analysis.material, analysis.pieces[i], enhance) for i in selection]


def _original(material: Material, piece: Piece) -> tuple[np.ndarray, int]:
    """区間の元の音（声の大きいチャンネル）と、その頭のサンプル位置。"""
    a = max(0, int(piece.start * OUTPUT_RATE))
    b = min(len(material.audio48), int(piece.end * OUTPUT_RATE))
    return _louder_channel(np.asarray(material.audio48[a:b], dtype=np.float32)), a


async def _enhance_piece(material: Material, source: Piece, enhance: Enhance) -> Piece:
    """雑音を除き、頭と末尾を話の切れ目へずらした写し。"""
    piece = replace(source)
    a = max(0, int((piece.start - ENHANCE_CONTEXT_SECONDS) * OUTPUT_RATE))
    b = min(len(material.audio48), int((piece.end + ENHANCE_CONTEXT_SECONDS) * OUTPUT_RATE))
    original = _louder_channel(np.asarray(material.audio48[a:b], dtype=np.float32))
    enhanced = await enhance(original)
    # 前後の文脈は除くときにだけ使い、頭と末尾を話の切れ目へずらして切る
    piece.start, piece.end = snap_edges(piece, enhanced, a / OUTPUT_RATE)
    lo = max(0, int(piece.start * OUTPUT_RATE) - a)
    hi = min(len(original), int(piece.end * OUTPUT_RATE) - a)
    piece.enhanced_snr = residual_snr(original[lo:hi], enhanced[lo:hi])
    # 文脈ごとの配列は抱えたままにしない
    piece.audio = enhanced[lo:hi].copy()
    return piece


def excerpt(material: Material, start: float, end: float) -> np.ndarray:
    """流れの start〜end 秒の元の音（声の大きいチャンネル）。区間を聴き比べるのに使う。"""
    end = min(end, start + EXCERPT_MAX_SECONDS)
    if end <= start:
        return np.zeros(0, np.float32)
    return _original(material, Piece(start, end, 0.0))[0]


def envelope(power: np.ndarray) -> dict:
    """素材全体の音の大きさ。levels は step 秒ごとの 0〜100（ENVELOPE_FLOOR_DB が 0、0 dBFS が 100）。"""
    step = max(1, -(-len(power) // ENVELOPE_MAX_POINTS))
    usable = len(power) // step * step
    blocks = power[:usable].reshape(-1, step).max(axis=1)
    if usable < len(power):
        blocks = np.r_[blocks, power[usable:].max()]
    db = 10 * np.log10(blocks + 1e-12)
    levels = np.clip((db - ENVELOPE_FLOOR_DB) / -ENVELOPE_FLOOR_DB * 100, 0, 100)
    return {"step": step * FRAME_SECONDS, "levels": np.rint(levels).astype(int).tolist()}


def describe(analysis: Analysis) -> dict:
    """studio のタイムラインに描く内容。時刻は流れ（ファイルを無音を挟んでつないだもの）の秒。"""
    material = analysis.material
    sources = material.sources

    def candidate(p: Piece) -> dict:
        v = p.voice
        return {
            "index": p.index,
            "file": sources.index(material.source_at(p.start)),
            "start": round(p.start, 2),
            "end": round(p.end, 2),
            "snr_db": round(p.background_snr, 1),
            "loudness_db": round(v.loudness_db, 1) if v else None,
            "pitch_hz": round(v.pitch_hz) if v and v.pitch_hz else None,
            "brightness_hz": round(v.brightness_hz) if v else None,
            "deviation": round(p.deviation, 2) if p.deviation is not None else None,
        }

    return {
        "files": [
            {"name": s.name, "offset": round(s.offset, 3), "seconds": round(s.seconds, 3)}
            for s in sources
        ],
        "truncated": material.truncated,
        "speakers": _speakers(analysis),
        "segments": [
            {"start": round(s.start, 2), "end": round(s.end, 2), "speaker": s.speaker}
            for s in analysis.segments
        ],
        "candidates": [candidate(p) for p in analysis.pieces],
        "preselected": preselect(analysis),
        "envelope": envelope(analysis.power),
        "limits": {
            "min_seconds": MIN_TOTAL_SECONDS,
            "target_seconds": TARGET_SECONDS,
            "max_seconds": MAX_REFERENCE_SECONDS,
        },
    }


def _speakers(analysis: Analysis) -> list[dict]:
    return [
        {"speaker": k, "seconds": round(v, 1), "chosen": k == analysis.speaker}
        for k, v in sorted(analysis.talk.items(), key=lambda kv: -kv[1])
    ]


def _report(analysis: Analysis, chosen: list[Piece]) -> dict:
    material = analysis.material

    def where(piece: Piece) -> dict:
        source = material.source_at(piece.start)
        return {
            "index": piece.index,
            "file": source.name,
            "start": round(piece.start - source.offset, 2),
            "end": round(piece.end - source.offset, 2),
            "snr_db": round(piece.score, 1),
        }

    return {
        "files": [{"name": s.name, "seconds": round(s.seconds, 1)} for s in material.sources],
        "truncated": material.truncated,
        "speakers": _speakers(analysis),
        "candidates": len(analysis.pieces),
        "seconds": round(sum(p.seconds for p in chosen), 1),
        "pieces": [where(p) for p in chosen],
    }


# MossFormer2 の重み。配布元は精度ごとのファイルを並べていて、mlx-audio の from_pretrained が
# 探す config.json と model.safetensors が無い。ほかの精度は使わないので、この1つだけを落とす
ENHANCE_WEIGHTS = "model_fp32.safetensors"


class MlxCurateBackend:
    """話者分離（Sortformer）と雑音除去（MossFormer2）。lifecycle からは1つのモデルに見える。"""

    name = "curate"

    def __init__(self, config: CurateConfig) -> None:
        self._config = config
        self.model = config.diarize_model
        self._diarizer = None
        self._enhancer = None

    def preflight(self) -> str | None:
        return None

    def is_downloaded(self) -> bool:
        from huggingface_hub.errors import LocalEntryNotFoundError

        if not hub.is_downloaded(self._config.diarize_model):
            return False
        try:
            self._enhance_weights(local_files_only=True)
        except (LocalEntryNotFoundError, OSError):
            return False
        return True

    def download(self) -> None:
        hub.download(self._config.diarize_model)
        self._enhance_weights()

    def _enhance_weights(self, *, local_files_only: bool = False) -> str:
        from huggingface_hub import hf_hub_download

        return hf_hub_download(
            self._config.enhance_model, ENHANCE_WEIGHTS, local_files_only=local_files_only
        )

    def load(self) -> None:
        import mlx.core as mx
        from mlx.utils import tree_unflatten
        from mlx_audio.sts.models.mossformer2_se.config import MossFormer2SEConfig
        from mlx_audio.sts.models.mossformer2_se.model import MossFormer2SEModel
        from mlx_audio.sts.models.mossformer2_se.mossformer2_se_wrapper import MossFormer2SE
        from mlx_audio.vad import load

        self._diarizer = load(self._config.diarize_model)
        # from_pretrained と同じ組み立てを、設定は既定のまま行う。MossFormer2SEModel は作るときに
        # nn.LayerNorm を mx.fast.layer_norm に差し替える（プロセス全体に効くが、計算は同じ）
        config = MossFormer2SEConfig()
        network = MossFormer2SE(config)
        weights = mx.load(self._enhance_weights())
        assert isinstance(weights, dict)  # safetensors は名前つきの辞書で返る
        network.update(tree_unflatten(list(weights.items())))
        self._enhancer = MossFormer2SEModel(config=config, model=network)

    def unload(self) -> None:
        import mlx.core as mx

        self._diarizer = None
        self._enhancer = None
        mx.clear_cache()

    def diarize(self, chunk: np.ndarray, state):
        """続きの1チャンクの話者分離。state は前のチャンクの続き（最初は None）。"""
        if self._diarizer is None:
            raise RuntimeError("curate model is not loaded")
        if state is None:
            state = self._diarizer.init_streaming_state()
        segments = []
        step = int(FEED_SECONDS * DIARIZE_RATE)
        for start in range(0, len(chunk), step):
            result, state = self._diarizer.feed(
                chunk[start : start + step],
                state,
                sample_rate=DIARIZE_RATE,
                threshold=0.5,
                min_duration=0.3,
            )
            segments += [
                Segment(float(s.start), float(s.end), int(s.speaker)) for s in result.segments
            ]
        return segments, state

    def enhance(self, samples: np.ndarray) -> np.ndarray:
        if self._enhancer is None:
            raise RuntimeError("curate model is not loaded")
        out = np.asarray(self._enhancer.enhance(samples), dtype=np.float32).reshape(-1)
        # 窓の端数の分だけ短く返るので、末尾を埋めて長さをそろえる
        return np.pad(out[: len(samples)], (0, max(0, len(samples) - len(out))))
