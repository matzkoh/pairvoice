"""音声合成バックエンド。mlx-audio の Irodori-TTS を薄く包む。

Irodori-TTS に参照テキストのパラメータは存在しない。話者性は参照音声の波形だけから
作られるので、渡すのは ref_audio と caption と sampler の設定だけ。

参照音声と caption は使用中のプロファイル（profiles.py）から合成のたびに読む。
プロファイルがまだ無ければ最初の合成で作る（bootstrap）。VoiceDesign（ref_audio=None）
は同じ caption でも文ごとに別人の声になるので、読み上げには使わず、プロファイルの
声を作るときだけに使う。

caption は speak() の引数 → プロファイル → caption.txt → 設定の既定値の順で解決する。
後ろの2つは bootstrap 前にだけ効く。studio からの試聴は引数で渡り、採用済みの版を
書き換えない。

caption と同じく sampler も3段で解決する（speak() の引数 → config.toml の
[tts.sampler] → モデル既定）。None は「モデル既定に任せる」の意味で、キーごと
渡さない。
"""

from __future__ import annotations

import dataclasses
import shutil
import uuid
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import mlx.core as mx
import numpy as np

from .config import TTSConfig
from .postprocess import normalize, trim
from .profiles import Profile, ProfileNotFound, ProfileStore

DEFAULT_SAMPLE_RATE = 48000
CAPTION_FILENAME = "caption.txt"
# bootstrap で caption から声を作るときに読ませる文。クローンの元になるので、
# 平叙・疑問・呼びかけを混ぜて約10秒にしてある
ANCHOR_TEXT = (
    "こんにちは。今日も一緒に作業を進めていきましょう。何かあったら、いつでも声をかけてくださいね。"
)
AUTO_PROFILE_NAME = "既定の声"


@dataclass(frozen=True)
class SpeechResult:
    path: Path
    relative_path: str
    duration: float
    peak_memory_gb: float | None


class MlxAudioBackend:
    name = "tts"
    # studio のプロファイル作成も同じ文を候補に読ませる（/health で申告する）
    anchor_text = ANCHOR_TEXT

    def __init__(self, config: TTSConfig, data_dir: Path | None = None) -> None:
        self._config = config
        self.model = config.model
        self._data_dir = data_dir or config.output_dir.parent
        self._profiles = ProfileStore(self._data_dir / "profiles")
        self._loaded = None

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
        SamplerConfig に1行足せば config.toml と /speak の両方に効く。
        例外は speaker_kv_min_t の1つだけで、これは None のまま残す。
        """
        merged: dict[str, object] = dict(dataclasses.asdict(self._config.sampler))
        if override:
            merged.update(override)
        resolved = {k: v for k, v in merged.items() if v is not None}
        if "speaker_kv_scale" in resolved and "speaker_kv_min_t" not in merged:
            # mlx-audio の巻き戻しを踏まないための明示。モデル既定の speaker_kv_min_t
            # （0.9）のままだと、t がそれを跨いだステップで話者 KV のスケールを戻す分岐に
            # 入るが、そこでキャッシュを組み直す複製数が単一コンテキスト前提の2で固定
            # されている。話者と caption を併せ持つモデルでは CFG のバッチが 4 なので、
            # 戻した次のステップで attention の concat がバッチ不一致で落ちる
            # （mlx-audio 0.5.7 でも未修正）。None なら巻き戻しの分岐自体に入らず、
            # スケールは全ステップに効く。
            #
            # speaker_kv_min_t が SamplerConfig の項目になった日には、指定された値を
            # 黙って捨てないよう手を引く（巻き戻しを望んだのはユーザーである）。
            resolved["speaker_kv_min_t"] = None
        return resolved

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

    def is_downloaded(self) -> bool:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError

        try:
            snapshot_download(self.model, local_files_only=True)
        except (LocalEntryNotFoundError, FileNotFoundError, OSError):
            return False
        return True

    def download(self) -> None:
        from huggingface_hub import snapshot_download

        snapshot_download(self.model)

    def _load_model(self):
        from mlx_audio.tts.utils import load_model

        # 注釈は Path だが、Hugging Face のリポジトリ名の文字列も受け付ける（中で解決する）
        return load_model(self.model)  # ty: ignore[invalid-argument-type]

    def load(self) -> None:
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        self._loaded = self._load_model()

    def unload(self) -> None:
        self._loaded = None
        mx.clear_cache()

    def speak(
        self,
        text: str,
        caption: str | None = None,
        sampler: Mapping[str, object] | None = None,
        design: bool = False,
        profile_id: str | None = None,
    ) -> SpeechResult:
        """合成する。design=True はプロファイルを使わず caption だけで声を作る（候補づくり用）。

        profile_id は studio の試聴用で、使用中でないプロファイルの声で鳴らす。
        """
        self._require_loaded()

        # 参照音声と caption は同じ1回の読みから取る。間で studio が切り替えても混ざらない
        profile = None if design else self._profile_for(profile_id)
        ref_audio = profile.reference if profile is not None else None
        resolved_caption, _ = self._resolve_caption(caption, profile)
        samples, sample_rate, peak = self._generate(
            text, ref_audio, resolved_caption, self.resolve_sampler(sampler)
        )

        path = self._config.output_dir / f"{uuid.uuid4()}.wav"
        self._write_wav(path, samples, sample_rate)

        return SpeechResult(
            path=path,
            relative_path=str(path.relative_to(self._data_dir)),
            duration=len(samples) / sample_rate,
            peak_memory_gb=peak,
        )

    def _profile_for(self, profile_id: str | None) -> Profile:
        if profile_id is None:
            return self._profiles.active() or self._bootstrap()
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ProfileNotFound(profile_id)
        return profile

    def _require_loaded(self):
        if self._loaded is None:
            raise RuntimeError("tts backend is not loaded")
        return self._loaded

    def _generate(
        self,
        text: str,
        ref_audio: Path | None,
        caption: str,
        sampler: Mapping[str, object],
    ) -> tuple[np.ndarray, int, float | None]:
        results = list(
            self._require_loaded().generate(
                text,
                ref_audio=str(ref_audio) if ref_audio is not None else None,
                caption=caption,
                # サンプラーの解決は resolve_sampler() の1箇所に閉じる。項目を増やす
                # ときに、ここへの配線を忘れて黙って効かないという事故を防ぐ
                **sampler,
            )
        )
        if not results:
            raise RuntimeError("tts backend produced no audio")

        result = results[0]
        sample_rate = int(getattr(result, "sample_rate", DEFAULT_SAMPLE_RATE))
        samples = np.asarray(result.audio, dtype=np.float32).reshape(-1)
        # NaN が1つでも混ざると trim / normalize の計算が NaN になって素通しになる
        samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
        samples = normalize(trim(samples, sample_rate), sample_rate)
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
            ANCHOR_TEXT, None, caption, self.resolve_sampler({"rng_seed": 0})
        )
        return self._profiles.create(
            name=AUTO_PROFILE_NAME,
            caption=caption,
            source="auto",
            write_reference=lambda path: self._write_wav(path, samples, sample_rate),
        )

    @staticmethod
    def _write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm = np.clip(samples, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm.tobytes())
