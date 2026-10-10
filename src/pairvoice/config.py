"""設定ファイルの読み込み。すべての項目に既定値があり、ファイルが無くても起動できる。"""

from __future__ import annotations

import dataclasses
import logging
import os
import tomllib
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("~/.config/pairvoice/config.toml")


@dataclass(frozen=True)
class LLMConfig:
    model: str = "mlx-community/gemma-4-e4b-it-8bit"
    max_tokens: int = 128
    idle_unload_seconds: int = 600
    load_timeout_seconds: int = 120


def default_data_root() -> Path:
    """データの置き場所。フックと同じく PAIRVOICE_DATA_ROOT → 既定の場所の順で決める。"""
    configured = os.environ.get("PAIRVOICE_DATA_ROOT")
    if configured:
        # 相対パスはいまの作業ディレクトリで絶対にする。launchd は / で起こすので、
        # 焼き込む値と、この場で雛形を置く場所がずれないように
        return Path(configured).expanduser().absolute()
    return Path.home() / "Library" / "Application Support" / "pairvoice"


@dataclass(frozen=True)
class SamplerConfig:
    """mlx-audio の generate() に渡すサンプラー。

    すべて None 既定で、None は「モデルの既定に任せる」を意味する（キーごと渡さない）。
    既定値を写さないのは、正本がモデルの config.json 側にあり、pairvoice に複製すると
    二重管理になるためである。

    フィールド名は generate() の引数名そのままにしてある。名前がずれた値は黙って
    捨てられるので、途中で変換する層を作らない。項目を増やすときはここに1行足すだけで
    config.toml と /synthesize の両方に効く。

    かつて 24kHz の参照音声のこもりを補うために cfg_scale_speaker を 1.5 まで下げていたが、
    参照音声を 44.1kHz に差し替えて原因が消えたので既定に戻した。「既定に戻した」がいまは None で表されている。
    ここを動かすときは1項目ずつ A/B すること。
    """

    num_steps: int | None = None
    cfg_guidance_mode: str | None = None
    cfg_scale_text: float | None = None
    cfg_scale_caption: float | None = None
    cfg_scale_speaker: float | None = None
    t_schedule_mode: str | None = None
    sway_coeff: float | None = None
    duration_scale: float | None = None
    seconds: float | None = None
    rng_seed: int | None = None
    cfg_min_t: float | None = None
    cfg_max_t: float | None = None
    context_kv_cache: bool | None = None
    speaker_kv_scale: float | None = None
    truncation_factor: float | None = None
    rescale_k: float | None = None
    rescale_sigma: float | None = None


@dataclass(frozen=True)
class ProfileConfig:
    """参照音声と、その声をどう喋らせるかの既定。

    既定値は特定の人物を指さない。使う声は各自が config.toml で指定する。

    ref_audio が None（未指定、または空文字）なら参照音声を使わず、caption だけで
    声を作る（Irodori-TTS の VoiceDesign）。指定したのにファイルが無い場合は
    VoiceDesign に落とさず misconfigured にする。
    """

    name: str = "reference"
    ref_audio: Path | None = None
    caption: str = "落ち着いた若い女性の声。ややゆっくりと、丸い声で穏やかに話す。"


@dataclass(frozen=True)
class TTSConfig:
    model: str = "mlx-community/Irodori-TTS-v4.1-Small-8bit"
    idle_unload_seconds: int = 600
    load_timeout_seconds: int = 120
    output_dir: Path = field(default_factory=lambda: default_data_root() / "generations")
    # output_dir の wav を、作ってからこの日数で消す。0 で消さない
    output_max_age_days: int = 7
    sampler: SamplerConfig = field(default_factory=SamplerConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)

    @property
    def data_root(self) -> Path:
        """合成と API が読み書きするデータの置き場所。生成音声の置き場所の親。"""
        return self.output_dir.parent


@dataclass(frozen=True)
class MuteConfig:
    auto_microphone: bool = True
    auto_audio_output: bool = True
    audio_output_wait_seconds: float = 5.0
    # 自分の再生はプロセス ID で除外するので、ここに足すのはほかのアプリだけでよい
    audio_output_ignore_processes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaybackConfig:
    """常駐サーバーが読み上げを鳴らすときの音量と待ち時間。"""

    volume: float = 1.0
    # ほかのアプリの音が鳴っている間に下げる音量
    duck_volume: float = 0.3
    # 先の読み上げが終わるのをこれ以上待ったものは、古くなったとみなして捨てる
    max_wait_seconds: float = 60.0


@dataclass(frozen=True)
class EvalConfig:
    """`pairvoice eval` の判定。style = "casual" でです・ます調を違反にする（タメ口の話者向け）。"""

    style: str = "any"


# 常駐サーバーの API はすべてこの下にある。根は studio の画面が使う
API_PREFIX = "/api"


@dataclass(frozen=True)
class Config:
    port: int = 17495
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    mute: MuteConfig = field(default_factory=MuteConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)
    source_path: Path | None = None
    source_mtime: float | None = None


class ConfigError(ValueError):
    """config.toml の値の型が違う。読み込みで止め、使う場面（合成・掃除）まで持ち越さない。"""


def _expand(value: object, base: Path) -> Path:
    # 相対パスは設定ファイルのディレクトリを基準にする。作業ディレクトリ基準だと、
    # launchd（/ で起こす）とシェルで別の場所を指す
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else base / path


def _coerce(where: str, value: object, hint: object) -> object:
    """TOML の値を型注釈に照らして検める。int は float の欄に通すが、bool は int に通さない。"""
    if isinstance(hint, types.UnionType) or typing.get_origin(hint) is typing.Union:
        # TOML に null は無いので、X | None の None 側は来ない
        (hint,) = [arg for arg in typing.get_args(hint) if arg is not type(None)]
    if typing.get_origin(hint) is tuple:
        # 1つだけなら配列でなく文字列で書きたくなる。tuple("zoom.us") は1文字ずつに割れる
        items = [value] if isinstance(value, str) else value
        if isinstance(items, list) and all(isinstance(item, str) for item in items):
            return tuple(items)
        expected = "文字列の配列"
    elif hint is bool:
        if isinstance(value, bool):
            return value
        expected = "true / false"
    elif hint is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        expected = "整数"
    elif hint is float:
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
        expected = "数値"
    elif hint in (str, Path):
        if isinstance(value, str):
            return value
        expected = "文字列"
    else:  # 項目を足したら上の分岐にも足す
        raise AssertionError(hint)
    raise ConfigError(f"{where} は{expected}で指定してください（{value!r}）")


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] は表（テーブル）で指定してください（{value!r}）")
    return value


def _build(cls, raw: dict, section: str, base: Path, *, nested: tuple[str, ...] = ()):
    """dataclass のフィールド名と一致するキーだけを拾い、残りは既定値に任せる。

    知らないキーは打ち間違いのことが多いが、版をまたいで設定を使い回すこともあるので
    止めずに警告する。nested は呼び出し側が別に組み立てる下位の表。
    """
    hints = typing.get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    for key in raw.keys() - known - set(nested):
        _log.warning("config.toml の [%s] %s は使われません（打ち間違い？）", section, key)
    kwargs = {}
    for name in known - set(nested):
        if name not in raw:
            continue
        value = _coerce(f"[{section}] {name}", raw[name], hints[name])
        if hints[name] in (Path, Path | None):
            value = _expand(value, base)
        kwargs[name] = value
    return cls(**kwargs)


_SECTIONS = ("port", "llm", "tts", "mute", "playback", "eval")


def load_config(path: Path | None = None) -> Config:
    resolved = Path(path if path is not None else DEFAULT_CONFIG_PATH).expanduser().absolute()
    if not resolved.exists():
        return Config(source_path=resolved, source_mtime=None)

    raw = tomllib.loads(resolved.read_text(encoding="utf-8"))
    base = resolved.parent
    for key in raw.keys() - set(_SECTIONS):
        _log.warning("config.toml の %s は使われません（打ち間違い？）", key)
    tts_raw = _section(raw, "tts")
    sampler_raw = _section(tts_raw, "sampler")
    profile_raw = dict(_section(tts_raw, "profile"))
    # TOML に null は無いので、空文字を「参照音声を使わない」と読む
    ref_audio = profile_raw.get("ref_audio")
    if isinstance(ref_audio, str) and not ref_audio.strip():
        profile_raw.pop("ref_audio")

    tts = _build(TTSConfig, tts_raw, "tts", base, nested=("sampler", "profile"))
    tts = dataclasses.replace(
        tts,
        sampler=_build(SamplerConfig, sampler_raw, "tts.sampler", base),
        profile=_build(ProfileConfig, profile_raw, "tts.profile", base),
    )

    port = raw.get("port", 17495)
    return Config(
        port=typing.cast(int, _coerce("port", port, int)),
        llm=_build(LLMConfig, _section(raw, "llm"), "llm", base),
        tts=tts,
        mute=_build(MuteConfig, _section(raw, "mute"), "mute", base),
        playback=_build(PlaybackConfig, _section(raw, "playback"), "playback", base),
        eval=_build(EvalConfig, _section(raw, "eval"), "eval", base),
        source_path=resolved,
        source_mtime=resolved.stat().st_mtime,
    )


def config_is_stale(config: Config) -> bool:
    """設定ファイルが読み込み後に更新されていれば True。再起動が必要なことを知らせる。"""
    if config.source_path is None or not config.source_path.exists():
        return False
    if config.source_mtime is None:
        return True
    # 古い mtime のファイルで置き換えられること（バックアップからの復元など）もあるので != で見る
    return config.source_path.stat().st_mtime != config.source_mtime
