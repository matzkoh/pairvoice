"""音声合成バックエンド。mlx-audio の Irodori-TTS を薄く包む。

Irodori-TTS に参照テキストのパラメータは存在しない。話者性は参照音声の波形だけから
作られるので、渡すのは ref_audio と caption と sampler の設定だけ。

参照音声と caption は使用中のプロファイル（profiles.py）から合成のたびに読む。
プロファイルがまだ無ければ最初の合成で作る（bootstrap）。VoiceDesign（ref_audio=None）
は同じ caption でも文ごとに別人の声になるので、読み上げには使わず、プロファイルの
声を作るときだけに使う。

caption は speak() の引数 → スタイル → プロファイル → caption.txt → 設定の既定値の
順で解決する。後ろの2つは bootstrap 前にだけ効く。studio からの試聴は引数で渡り、
採用済みの版を書き換えない。

sampler は項目ごとに speak() の引数 → スタイル → config.toml の [tts.sampler] →
SamplerConfig の既定 → モデル既定の順で決まる。None は「モデル既定に任せる」の意味で、キーごと渡さない。

声（プロファイル）とスタイル（styles.py）は、API の利用者がリクエストごとに名前で選べる。

読む文は合成のたびに読み辞書（reading.py）で読みに開く。フック・`pairvoice say`・studio の
試聴のどこから来ても同じ読みになる。

声の個体は、参照音声から作る話者の表現（speaker_state、約6トークン/秒 × 768次元）で
決まる。studio の「2択で絞り込む」と「混ぜる」は、いくつかのもとの声の表現を重みで混ぜた声を
合成し（mix）、もとの声どうしの位置関係を話者ベクトル（表現の時間平均）で測る。表現を差し込む
公開の口は mlx-audio に無いので、encode_conditions_full を包んで差し替える。
プロファイルの声も、参照音声から作った表現を取っておいて同じ口から差し込む（参照音声の
符号化を合成のたびにやり直さない）。

読み上げは全文を1回で合成する。合成しながら鳴らすとき（speak_streaming）は、波形を
解けた順に呼び手へ渡す。モデルは全文の潜在表現を一度に作ってから、2秒ずつ波形に解くので、
解き終わった所から鳴らせる。渡す音は一括と同じ切り方にし、音量は声ごとに覚えた倍率でそろえる
（1件ごとの倍率は、全部を解き終わるまで決まらない）。

設定の split_sentences を入れると、文ごとに合成してできた文から返す（speak_sentences）。
鳴り始めは早くなるが、文の間の抑揚がつながらない。
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import logging
import shutil
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path

import mlx.core as mx
import numpy as np

from . import hub, speedups
from .checker import split_sentences
from .config import TTSConfig
from .postprocess import StreamCut, cut_lead, level_gain_db, normalize, trim
from .profiles import (
    PROFILES_DIRNAME,
    MixVoice,
    Profile,
    ProfileNotFound,
    ProfileRejected,
    ProfileStore,
)
from .reading import DICT_FILENAME, apply_dict, load_dict
from .styles import STYLES_FILENAME, StyleStore
from .wav import band_limited, concat_wavs, read_mono, write_mono

DEFAULT_SAMPLE_RATE = 48000
CAPTION_FILENAME = "caption.txt"
# bootstrap で caption から声を作るときに読ませる文。クローンの元になるので、
# 平叙・疑問・呼びかけを混ぜて約10秒にしてある
ANCHOR_TEXT = (
    "こんにちは。今日も一緒に作業を進めていきましょう。何かあったら、いつでも声をかけてくださいね。"
)
AUTO_PROFILE_NAME = "既定の声"
# もとの声の話者の表現を取っておく数。2択の1セッションのもとの声（数人）と、その間の行き来で足りる
SPEAKER_STATE_CACHE_SIZE = 32
# 最後の文以外は、見積もる長さをこれだけ縮めて合成する。文を1つだけ渡すとモデルは長さを
# 中身より長く見積もり、余りを語尾に続く声で埋める。語尾との間が空かないので trim で
# 落ちず、文の間に挟まって聞こえる。0.9 で余りは消え、話す速さは約6%上がる（0.8 だと
# 速さが目立つ）。最後の文の余りは、全文を1回で合成するときと同じく trim が落とす
NON_FINAL_DURATION_SCALE = 0.9
# 本文の前に読ませて切り落とす前置き。Irodori-TTS は頭に間を置かずに話し始め、最初の語を
# よく崩す（ノーション → 山さん、コグニート → アブミート）。前置きの後の間から本文を
# 始めさせると崩れない（文頭の語が合った率: 前置きなし 15/30、「ええ。」30/30、「はい。」27/30）
LEAD_TEXT = "ええ。"
# 声ごとに覚えておく音量の倍率の数と、覚えておく声の数。合成しながら鳴らすときは、
# 直近の倍率の中央値でそろえる（同じ声なら発話ごとの差は ±0.7dB に収まる）
LEVEL_MEMORY = 8
LEVEL_VOICES = 64
# 話者の表現の控え（_reference_state）を読み書きするときに握りつぶす失敗。控えが無くても作り直せる
_CACHE_ERRORS = (OSError, ValueError, RuntimeError)
try:
    _MLX_AUDIO_VERSION = importlib.metadata.version("mlx-audio")
except importlib.metadata.PackageNotFoundError:
    _MLX_AUDIO_VERSION = "unknown"


_log = logging.getLogger(__name__)


class AudioNotFound(Exception):
    """指定された音声がデータの置き場所に無い（外を指している・消された）。"""


class UnmuffleUnsupported(Exception):
    """いまの合成モデルには、こもりの向き（muffle/<モデル>.npy）が無い。"""


def muffle_direction_path(model: str) -> Path:
    """合成モデルごとの、こもりの向き。scripts/muffle_direction.py が作る。"""
    return Path(__file__).parent / "muffle" / f"{model.replace('/', '--')}.npy"


def resolve_data_audio(data_dir: Path, relative: str) -> Path:
    """データの置き場所からの相対パスを、置き場所の中の wav に解決する。"""
    root = data_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path.suffix != ".wav" or not path.is_file():
        raise AudioNotFound(relative)
    return path


@dataclass(frozen=True)
class SpeechResult:
    path: Path
    relative_path: str
    duration: float
    peak_memory_gb: float | None


@dataclass(frozen=True)
class _Voice:
    """1回の読み上げで使う声。文ごとに合成しても、途中で studio が切り替えた声が混ざらない。"""

    ref_audio: Path | None
    caption: str
    sampler: Mapping[str, object]
    # 差し込む話者の表現。None なら ref_audio を mlx-audio に符号化させる
    speaker: mx.array | None = None
    # 声のプロファイル。プロファイルの声でなければ None
    profile_id: str | None = None


def _scrubbed(samples: np.ndarray) -> np.ndarray:
    # NaN が1つでも混ざると trim / normalize の計算が NaN になって素通しになる
    return np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)


class _Live:
    """合成しながら鳴らす1回ぶんの合成。解けた波形から鳴らせる分を切り出し、emit に渡す。"""

    def __init__(
        self, emit: Callable[[np.ndarray], None], gain: float, sample_rate: int, *, lead: bool
    ) -> None:
        self._emit = emit
        self._gain = np.float32(gain)
        self._cutter = StreamCut(sample_rate, lead=lead)

    def __call__(self, wave: np.ndarray, remaining: int) -> None:
        piece = self._cutter.push(_scrubbed(wave), remaining)
        if piece is not None:
            self._emit(piece * self._gain)

    @property
    def cut(self) -> int | None:
        """前置きを切った位置。全部を解き終わる前に鳴らし始めていなければ None。"""
        return self._cutter.cut


class _Levels:
    """声ごとに、音量をそろえた倍率を覚えておく。

    合成しながら鳴らすときは、その発話をそろえる倍率が全部を解き終わるまで分からないので、
    同じ声と話し方（プロファイルと caption）で前にそろえた倍率の中央値を使う。
    """

    def __init__(self) -> None:
        self._gains_db: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()

    def remember(self, voice: _Voice, gain_db: float) -> None:
        if voice.profile_id is None:
            return
        key = (voice.profile_id, voice.caption)
        self._gains_db.setdefault(key, deque(maxlen=LEVEL_MEMORY)).append(gain_db)
        self._gains_db.move_to_end(key)
        if len(self._gains_db) > LEVEL_VOICES:
            self._gains_db.popitem(last=False)

    def gain(self, voice: _Voice) -> float | None:
        """この声でこれまでにそろえた倍率。まだ読んでいない声なら None。"""
        if voice.profile_id is None:
            return None
        gains_db = self._gains_db.get((voice.profile_id, voice.caption))
        return float(10 ** (np.median(gains_db) / 20)) if gains_db else None


class MlxAudioBackend:
    name = "tts"
    # studio のプロファイル作成も同じ文を候補に読ませる（/health で申告する）
    anchor_text = ANCHOR_TEXT

    def __init__(self, config: TTSConfig, data_dir: Path | None = None) -> None:
        self._config = config
        self.model = config.model
        self._data_dir = data_dir or config.data_root
        self._profiles = ProfileStore(self._data_dir / PROFILES_DIRNAME)
        self._styles = StyleStore(self._data_dir / STYLES_FILENAME)
        self._loaded = None
        # 次の合成で差し込む話者の表現。合成の間だけ立てる（_speaking_as）
        self._injected_speaker = None
        self._speaker_injection_ready = False
        self._speaker_states: OrderedDict[tuple[Path, int], mx.array] = OrderedDict()
        # こもりの向き。None はまだ読んでいない、False はいまのモデルの向きが無い
        self._muffle: np.ndarray | bool | None = None
        # モデルを降ろしても声の音量は変わらないので、unload では消さない
        self._levels = _Levels()

    def configure(self, config: TTSConfig) -> None:
        """読み直した設定を当てる。モデル名と置き場所は変わらない（変わるなら再起動する）。"""
        self._config = config

    @property
    def caption_path(self) -> Path:
        return self._data_dir / CAPTION_FILENAME

    def resolve_caption(self, override: str | None = None) -> tuple[str, str]:
        """使う caption と、その出どころ（request / profile / file / config）を返す。

        プロファイルを合成のたびに読むので、studio で採用した版が再起動なしに効く。
        file（caption.txt）と config はプロファイルができる前（bootstrap 前）にだけ効く。
        """
        return self._resolve_caption(override, self._profiles.active())

    def _resolve_caption(self, override: str | None, profile: Profile | None) -> tuple[str, str]:
        # 空の caption は「caption なしで読む」の意味で、次の段へ落とさない（モデルは空を
        # mask して参照音声だけで読む）。未指定は None
        if override is not None:
            return override.strip(), "request"
        if profile is not None:
            return profile.caption.strip(), "profile"
        try:
            from_file = self.caption_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            from_file = ""
        if from_file.strip():
            return from_file.strip(), "file"
        return self._config.profile.caption, "config"

    def resolve_sampler(self, override: Mapping[str, object] | None = None) -> dict[str, object]:
        """generate() に渡すサンプラーを決める（config → 上書きの2段）。

        None は「モデル既定に任せる」なのでキーごと落とす。項目ごとの配線を持たないので、
        SamplerConfig に1行足せば config.toml と /synthesize の両方に効く。
        """
        merged: dict[str, object] = dict(dataclasses.asdict(self._config.sampler))
        if override:
            merged.update(override)
        return {k: v for k, v in merged.items() if v is not None}

    def describe_profile(self) -> dict[str, str] | None:
        profile = self._profiles.active()
        if profile is None:
            return None
        return {"id": profile.id, "name": profile.name, "source": profile.source}

    def preflight(self) -> str | None:
        # bootstrap の後は設定の参照音声を読まない。消えていても困らない
        if self._profiles.active() is not None:
            return None
        ref_audio = self._config.profile.ref_audio
        if ref_audio is None:
            return None
        # ディレクトリは bootstrap の複製で落ちるので、無いのと同じに扱う
        if not ref_audio.is_file() or ref_audio.stat().st_size == 0:
            return "profile_missing"
        return None

    @property
    def sample_rate(self) -> int:
        """合成する波形のサンプルレート。鳴らす側が、波形が届く前に出力を用意するのに使う。"""
        return int(getattr(self._loaded, "sample_rate", DEFAULT_SAMPLE_RATE))

    def is_downloaded(self) -> bool:
        return hub.is_downloaded(self.model)

    def download(self) -> None:
        hub.download(self.model)

    def _load_model(self):
        from mlx_audio.tts.utils import load_model

        # 注釈は Path だが、Hugging Face のリポジトリ名の文字列も受け付ける（中で解決する）
        return load_model(self.model)  # ty: ignore[invalid-argument-type]

    def load(self) -> None:
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = self._load_model()
        # 話者の表現の差し込みより先に入れる（差し込みが、まとめた encode_conditions_full を包む）
        if not speedups.install(self._loaded):
            _log.warning(
                "合成を速める差し替えを入れられませんでした（mlx-audio の中身が想定と違う）"
            )
        self._install_speaker_injection()

    def unload(self) -> None:
        self._loaded = None
        self._speaker_states.clear()
        mx.clear_cache()

    def _install_speaker_injection(self) -> None:
        """混ぜた話者の表現を差し込む口を、読み込んだモデルに付ける（mlx-audio に公開の口が無い）。

        encode_conditions_full は継続時間の予測と sampler の2か所から呼ばれる。どちらにも同じ
        表現が入るよう、呼び出し元ではなくこの関数そのものを包む。差し込む間は参照音声の
        符号化（DACVAE）も空の1パッチで済ませる。符号化した表現はどのみち捨てる。
        """
        model = self._loaded
        dit = getattr(model, "model", None)
        original = getattr(dit, "encode_conditions_full", None)
        original_encode_refs = getattr(model, "_encode_ref_audios", None)
        self._speaker_injection_ready = False
        if model is None or dit is None or original is None or original_encode_refs is None:
            return

        def encode_conditions_full(*args, **kwargs):
            out = original(*args, **kwargs)
            state = self._injected_speaker
            if state is None or kwargs.get("ref_latent") is None:
                return out
            # 戻り値の並びが変わったら、黙って別の値を差し込まずに止める
            if len(out) != 6:
                raise RuntimeError("encode_conditions_full の戻り値の形が想定と違う")
            text_state, text_mask, _, _, caption_state, caption_mask = out
            speaker_mask = mx.ones((1, state.shape[1]), dtype=mx.bool_)
            return text_state, text_mask, state, speaker_mask, caption_state, caption_mask

        def encode_ref_audios(audios, *args, **kwargs):
            if self._injected_speaker is None:
                return original_encode_refs(audios, *args, **kwargs)
            cfg = model.config.dit
            empty = mx.zeros((1, cfg.speaker_patch_size, cfg.latent_dim))
            return empty, mx.ones((1, cfg.speaker_patch_size), dtype=mx.bool_)

        dit.encode_conditions_full = encode_conditions_full
        model._encode_ref_audios = encode_ref_audios
        self._speaker_injection_ready = True

    @contextmanager
    def _speaking_as(self, state: mx.array):
        """この中の合成だけ、話者の表現を state に差し替える。"""
        if not self._speaker_injection_ready:
            raise RuntimeError("この TTS モデルには話者の表現を差し込めない")
        self._injected_speaker = state
        try:
            yield
        finally:
            self._injected_speaker = None

    def _speaker_state(self, path: Path) -> mx.array:
        """参照音声の話者の表現 (1, T, 768)。同じファイルは測り直さない。"""
        return self._cached_state(path, lambda: self._encode_file(path))

    def _encode_file(self, path: Path) -> mx.array:
        return self._encode_speaker(self._require_loaded()._load_ref_waveform(str(path)))

    def _reference_state(self, path: Path) -> mx.array:
        """プロファイルの参照音声の話者の表現。ディスクにも取っておく。

        参照音声から作ると1人 0.5〜0.6 秒かかり、メモリの控えはモデルを降ろすと消える。
        控えはプロファイルのフォルダーに置き（プロファイルを消せば一緒に消える）、モデル・
        mlx-audio のバージョン・参照音声の更新時刻で名前を分け、古い控えは作り直すときに消す。
        保存と読み込みで値は変わらない。記録の音声（mix の audio）はプロファイルではないので取っておかない。
        """

        def make() -> mx.array:
            prefix = f"{path.stem}.{str(self.model).replace('/', '--')}."
            saved = path.parent / (
                f"{prefix}{_MLX_AUDIO_VERSION}.{path.stat().st_mtime_ns}.speaker.npy"
            )
            if saved.is_file():
                with suppress(*_CACHE_ERRORS):
                    loaded = mx.load(str(saved))
                    if isinstance(loaded, mx.array):
                        return loaded
            state = self._encode_file(path)
            mx.eval(state)
            for stale in path.parent.glob(f"{prefix}*.speaker.npy"):
                stale.unlink(missing_ok=True)
            with suppress(*_CACHE_ERRORS):
                mx.save(str(saved), state)
            return state

        return self._cached_state(path, make)

    def _tokens_state(self, path: Path) -> mx.array:
        """畳み込んだ声の話者の表現 (1, T, 768)。.npy から読む。"""
        return self._cached_state(path, lambda: mx.array(np.load(path)[None]))

    def _cached_state(self, path: Path, make: Callable[[], mx.array]) -> mx.array:
        key = (path, path.stat().st_mtime_ns)
        cached = self._speaker_states.get(key)
        if cached is not None:
            self._speaker_states.move_to_end(key)
            return cached
        state = make()
        mx.eval(state)
        self._speaker_states[key] = state
        while len(self._speaker_states) > SPEAKER_STATE_CACHE_SIZE:
            self._speaker_states.popitem(last=False)
        return state

    def _encode_speaker(self, waveform: mx.array) -> mx.array:
        from mlx_audio.tts.models.irodori_tts.model import patch_sequence_with_mask

        model = self._require_loaded()
        latent, mask = model._encode_ref_audios([waveform])
        dit = model.model
        latent_p, mask_p = patch_sequence_with_mask(latent, mask, dit.cfg.speaker_patch_size)
        return dit.speaker_norm(dit.speaker_encoder(latent_p, mask_p))

    def speaker_tokens(self, samples: np.ndarray) -> np.ndarray:
        """48kHz モノラルの音の話者の表現 (T, 768)。素材を畳み込むときに使い、取っておかない
        （何十本も通すと、プロファイルの声の表現が取っておく枠から押し出される）。"""
        model = self._require_loaded()
        # ファイルから読むときと同じ (1, samples) の形にそろえる
        waveform = model._load_ref_waveform(mx.array(np.asarray(samples, np.float32)))
        state = self._encode_speaker(waveform)
        return np.asarray(state[0], dtype=np.float32)

    def resolve_audio(self, relative: str) -> Path:
        return resolve_data_audio(self._data_dir, relative)

    def speaker_vector(self, path: Path) -> list[float]:
        """話者の表現の時間平均。声どうしの近さを測る物差しにする。"""
        self._require_loaded()
        state = self._speaker_state(path)
        return np.asarray(mx.mean(state, axis=1), dtype=np.float32).reshape(-1).tolist()

    def band_limited(self, path: Path) -> bool:
        """wav の上の帯域が切れているか（こもった音か）。モデルは使わない。"""
        return band_limited(*read_mono(path))

    def mix_tokens(
        self, mix: Sequence[tuple[Path | MixVoice, float]], unmuffle: float = 0.0
    ) -> np.ndarray:
        """混ぜた話者の表現 (T, 768)。畳み込んだ声と同じ形で、プロファイルに保存できる。"""
        self._require_loaded()
        state = self._mixed_speaker_state(self._mix_parts(mix), unmuffle)
        return np.asarray(state[0], dtype=np.float32)

    @property
    def can_unmuffle(self) -> bool:
        """いまの合成モデルに、こもりの向きがあるか。"""
        return self._muffle_direction() is not None

    def _muffle_direction(self) -> np.ndarray | None:
        # モデルは起動中に替わらない（替えるなら再起動する）ので、1回だけ読む
        if self._muffle is None:
            path = muffle_direction_path(str(self.model))
            if path.is_file():
                self._muffle = np.load(path).astype(np.float32)
            else:
                self._muffle = False
        return self._muffle if isinstance(self._muffle, np.ndarray) else None

    def _mix_parts(
        self, mix: Sequence[tuple[Path | MixVoice, float]]
    ) -> list[tuple[Path | Profile, float]]:
        """混ぜる声をプロファイルに引く（1回の合成で1回だけ）。"""
        return [
            (source if isinstance(source, Path) else self._mix_profile(source.key), weight)
            for source, weight in mix
        ]

    def _mix_profile(self, key: str) -> Profile:
        # resolve はランダムの声からメンバーを1つ選んでしまうので、find で引いて断る
        profile = self._profiles.find(key)
        if profile is None:
            raise ProfileNotFound(key)
        if profile.is_random:
            raise ProfileRejected("ランダムの声は混ぜられません")
        return profile

    def _profile_state(self, profile: Profile) -> mx.array | None:
        """読み上げでその声に差し込む表現。畳み込んだ表現が今のモデルのものなら、それを使う。"""
        tokens = profile.tokens_for(str(self.model))
        if tokens is not None:
            return self._tokens_state(tokens)
        if profile.reference is not None:
            return self._reference_state(profile.reference)
        return None

    def _part_state(self, part: Path | Profile) -> mx.array:
        state = self._speaker_state(part) if isinstance(part, Path) else self._profile_state(part)
        if state is None:
            raise ProfileRejected(f"{part.name} は混ぜられません")
        return state

    def _mixed_speaker_state(
        self, mix: Sequence[tuple[Path | Profile, float]], unmuffle: float = 0.0
    ) -> mx.array:
        # 声は読み上げと同じ表現で混ぜる（両端が読み上げの声と一致する）。
        # 表現は時間方向の列で長さがもとの声ごとに違うので、短い方にそろえてから重みで足す。
        # 重みは足して1に直す（倍率を変えると声ではなく話者の効き具合が変わる）。負の重みは
        # 外挿で、その声から遠ざかる向きへ伸ばす（studio の2択が片側に使う）
        states = [self._part_state(part) for part, _ in mix]
        length = min(int(s.shape[1]) for s in states)
        total = sum(weight for _, weight in mix)
        if length == 0 or total <= 0:
            raise ValueError("mix weights must sum to a positive value and references be non-empty")
        mixed = mx.zeros_like(states[0][:, :length])
        for (_, weight), state in zip(mix, states, strict=True):
            mixed = mixed + (weight / total) * state[:, :length]
        if unmuffle:
            # こもりの向きを全フレームに足す（上の帯域と張りが戻る。多く足すほど声も変わる）
            direction = self._muffle_direction()
            if direction is None:
                raise UnmuffleUnsupported(str(self.model))
            mixed = mixed + unmuffle * mx.array(direction)[None, None]
        return mixed

    def speak(
        self,
        text: str,
        caption: str | None = None,
        sampler: Mapping[str, object] | None = None,
        design: bool = False,
        profile_id: str | None = None,
        mix: Sequence[tuple[Path | MixVoice, float]] | None = None,
        style: str | None = None,
        unmuffle: float = 0.0,
    ) -> SpeechResult:
        """合成する。design=True はプロファイルを使わず caption だけで声を作る（候補づくり用）。

        profile_id は使用中でないプロファイルの声で鳴らす。ID のほか名前でも引ける。
        style はスタイルの名前で、caption と sampler の既定をそのスタイルに替える。
        引数の caption と sampler はスタイルより優先する。
        mix は (参照音声か声, 重み) の組で、その声たちの話者の表現を重みで混ぜた声で鳴らす
        （2択で絞り込む・混ぜる）。profile_id は使わない。unmuffle は mix に足すこもりの向きの量。
        text は読み辞書で読みに開いてから読む。
        """
        voice = self._voice(caption, sampler, design, profile_id, mix, style, unmuffle)
        return self._save(*self._generate(self._read(text), voice))

    def speak_streaming(
        self,
        text: str,
        caption: str | None,
        profile_id: str | None,
        style: str | None,
        emit: Callable[[np.ndarray], None],
    ) -> SpeechResult:
        """合成しながら、鳴らせる波形を解けた順に emit へ渡す。引数は speak() と同じ意味。

        保存する wav は speak() と同じ。emit に渡す波形は、頭と末尾を同じ所で切り、音量を
        この声で覚えた倍率でそろえたもの。初めての声と、切り方が全部を解くまで決まらなかった
        ときは、解き終わってから wav と同じ波形を一度に渡す。
        """
        voice = self._voice(caption=caption, profile_id=profile_id, style=style)
        return self._save(*self._generate(self._read(text), voice, emit))

    def speak_sentences(
        self,
        text: str,
        caption: str | None = None,
        profile_id: str | None = None,
        style: str | None = None,
    ) -> Iterator[SpeechResult]:
        """合成し、できた順に返す。引数は speak() と同じ意味。

        設定の split_sentences が無ければ全文を1文として合成する。声は最初の文の前に1回だけ決める。
        """
        voice = self._voice(caption=caption, profile_id=profile_id, style=style)
        scale = voice.sampler.get("duration_scale")
        scale = scale if isinstance(scale, int | float) else 1.0
        shortened = dataclasses.replace(
            voice, sampler={**voice.sampler, "duration_scale": scale * NON_FINAL_DURATION_SCALE}
        )
        read = self._read(text)
        sentences = split_sentences(read) if self._config.split_sentences else [read]
        *leading, last = sentences or [text]
        for sentence in leading:
            yield self._save(*self._generate(sentence, shortened))
        yield self._save(*self._generate(last, voice))

    def join(self, parts: Sequence[SpeechResult]) -> SpeechResult:
        """文ごとの wav を1本につなぐ。記録（corpus）が1回の読み上げを1本の音声で指すため。"""
        if len(parts) == 1:
            return parts[0]
        path = self._new_output_path()
        path.write_bytes(concat_wavs([part.path.read_bytes() for part in parts], gap_seconds=0.0))
        return self._result(
            path,
            duration=sum(part.duration for part in parts),
            peak=max(
                (part.peak_memory_gb for part in parts if part.peak_memory_gb is not None),
                default=None,
            ),
        )

    def _read(self, text: str) -> str:
        return apply_dict(text, load_dict(self._data_dir / DICT_FILENAME))

    def _voice(
        self,
        caption: str | None = None,
        sampler: Mapping[str, object] | None = None,
        design: bool = False,
        profile_id: str | None = None,
        mix: Sequence[tuple[Path | MixVoice, float]] | None = None,
        style: str | None = None,
        unmuffle: float = 0.0,
    ) -> _Voice:
        self._require_loaded()
        # 合成の前に引く。名前の打ち間違いで、生成を済ませてから断ることのないように
        if style is not None:
            chosen = self._styles.get(style)
            caption = caption if caption is not None else chosen.caption
            sampler = {**chosen.sampler, **(sampler or {})}

        if mix:
            # 参照音声は合成を参照つきの経路に入れるためだけに渡す。表現は差し込んだ方が使われる
            parts = self._mix_parts(mix)
            first = parts[0][0]
            return _Voice(
                first if isinstance(first, Path) else first.reference,
                (caption or "").strip(),
                self.resolve_sampler(sampler),
                self._mixed_speaker_state(parts, unmuffle),
            )

        # 参照音声と caption は同じ1回の読みから取る。間で studio が切り替えても混ざらない
        profile = None if design else self._profile_for(profile_id)
        ref_audio = profile.reference if profile is not None else None
        resolved_caption, _ = self._resolve_caption(caption, profile)
        speaker = None
        if self._speaker_injection_ready and profile is not None:
            speaker = self._profile_state(profile)
        profile_id = profile.id if profile is not None else None
        return _Voice(
            ref_audio, resolved_caption, self.resolve_sampler(sampler), speaker, profile_id
        )

    def save(self, samples: np.ndarray, sample_rate: int) -> SpeechResult:
        """合成していない音（素材から組んだ参照音声など）を、合成した wav と同じ所に置く。"""
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        return self._save(samples, sample_rate, None)

    def _save(self, samples: np.ndarray, sample_rate: int, peak: float | None) -> SpeechResult:
        path = self._new_output_path()
        write_mono(path, samples, sample_rate)
        return self._result(path, duration=len(samples) / sample_rate, peak=peak)

    def _new_output_path(self) -> Path:
        return self._config.output_dir / f"{uuid.uuid4()}.wav"

    def _result(self, path: Path, *, duration: float, peak: float | None) -> SpeechResult:
        return SpeechResult(
            path=path,
            relative_path=str(path.relative_to(self._data_dir)),
            duration=duration,
            peak_memory_gb=peak,
        )

    def _profile_for(self, profile_id: str | None) -> Profile:
        # ランダムの声は、ここでメンバーから1つを選ぶ
        return self._profiles.resolve(profile_id) or self._bootstrap()

    def _require_loaded(self):
        if self._loaded is None:
            raise RuntimeError("tts backend is not loaded")
        return self._loaded

    def _generate(
        self, text: str, voice: _Voice, emit: Callable[[np.ndarray], None] | None = None
    ) -> tuple[np.ndarray, int, float | None]:
        """emit があれば、鳴らせる波形を解けた順に渡す。返す音は emit の有無で変わらない。

        解けた順に渡せるのは、その声でそろえた音量を覚えていて、頭の切り方が全部を解く前に
        決まったとき。そうでなければ、解き終わってから、返す音と同じ波形を一度に渡す。
        """
        gain = None if emit is None else self._levels.gain(voice)

        def listener(*, lead: bool) -> _Live | None:
            if emit is None or gain is None:
                return None
            return _Live(emit, gain, self.sample_rate, lead=lead)

        live = listener(lead=True)
        samples, sample_rate, peak = self._generate_raw(LEAD_TEXT + text, voice, live)
        body = cut_lead(samples, sample_rate)
        if body is None and live is not None and live.cut is not None:
            # 鳴らし始めた後で、全体を見ると前置きを切れなかった（後からずっと大きい音が来た）。
            # 作り直すと鳴らした音と別の読み上げが残るので、鳴らし始めに切った位置から後ろを使う
            body = samples[live.cut :]
        if body is None:
            # 前置きの後に間が無いと、前置きを残さずには切れない
            live = listener(lead=False)
            samples, sample_rate, peak = self._generate_raw(text, voice, live)
            body = samples
        final = self._leveled(voice, trim(body, sample_rate), sample_rate)
        if emit is not None and (live is None or live.cut is None):
            emit(final)
        return final, sample_rate, peak

    def _leveled(self, voice: _Voice, trimmed: np.ndarray, sample_rate: int) -> np.ndarray:
        """音量をそろえる。そろえた倍率は声ごとに覚えておく。"""
        gain_db = level_gain_db(trimmed, sample_rate)
        if gain_db is not None:
            self._levels.remember(voice, gain_db)
        return normalize(trimmed, sample_rate)

    def _generate_raw(
        self, text: str, voice: _Voice, sink: speedups.DecodedSink | None = None
    ) -> tuple[np.ndarray, int, float | None]:
        """sink があれば、返す波形と同じものを、解けた順に sink にも渡す。"""
        model = self._require_loaded()
        speaking = nullcontext() if voice.speaker is None else self._speaking_as(voice.speaker)
        listening = nullcontext() if sink is None else speedups.streaming(model, sink)
        with speaking, listening:
            results = list(
                model.generate(
                    text,
                    ref_audio=str(voice.ref_audio) if voice.ref_audio is not None else None,
                    caption=voice.caption,
                    # サンプラーの解決は resolve_sampler() の1箇所に閉じる。項目を増やす
                    # ときに、ここへの配線を忘れて黙って効かないという事故を防ぐ
                    **voice.sampler,
                )
            )
        if not results:
            raise RuntimeError("tts backend produced no audio")

        result = results[0]
        sample_rate = int(getattr(result, "sample_rate", DEFAULT_SAMPLE_RATE))
        samples = _scrubbed(np.asarray(result.audio, dtype=np.float32).reshape(-1))
        return samples, sample_rate, getattr(result, "peak_memory_usage", None)

    def _bootstrap(self) -> Profile:
        """最初のプロファイルを作る。設定に参照音声があれば取り込み、無ければ caption から作る。

        以降は設定の [tts.profile] と caption.txt を読まない（正本は profiles/ に移る）。
        """
        caption, _ = self.resolve_caption()
        configured = self._config.profile.ref_audio
        if configured is not None:
            return self._profiles.create(
                name=self._config.profile.name,
                caption=caption,
                source="import",
                write_reference=lambda path: shutil.copyfile(configured, path),
            )

        # seed を固定するのは、同じ caption なら誰の環境でも同じ既定の声になるようにするため
        samples, sample_rate, _ = self._generate(
            ANCHOR_TEXT, _Voice(None, caption, self.resolve_sampler({"rng_seed": 0}))
        )
        return self._profiles.create(
            name=AUTO_PROFILE_NAME,
            caption=caption,
            source="auto",
            write_reference=lambda path: write_mono(path, samples, sample_rate),
        )
