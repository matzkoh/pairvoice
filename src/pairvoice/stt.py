"""聞き取りバックエンド。mlx-audio の STT を薄く包むだけで、状態管理は lifecycle が持つ。"""

from __future__ import annotations

from pathlib import Path

from . import hub
from .config import STTConfig


class MlxSttBackend:
    name = "stt"

    def __init__(self, config: STTConfig) -> None:
        self._config = config
        self.model = config.model
        self._loaded = None

    def preflight(self) -> str | None:
        return None

    def is_downloaded(self) -> bool:
        return hub.is_downloaded(self.model)

    def download(self) -> None:
        hub.download(self.model)

    def load(self) -> None:
        from mlx_audio.stt.utils import load_model

        self._loaded = load_model(self.model)

    def unload(self) -> None:
        import mlx.core as mx

        self._loaded = None
        mx.clear_cache()

    def transcribe(self, path: Path) -> str:
        if self._loaded is None:
            raise RuntimeError("stt model is not loaded")
        return self._loaded.generate(str(path), language=self._config.language).text.strip()
