"""要約バックエンド。mlx-lm を薄く包むだけで、状態管理は lifecycle が持つ。"""

from __future__ import annotations

import logging

import mlx.core as mx
from mlx_lm import generate as mlx_generate
from mlx_lm import load as mlx_load

from .config import LLMConfig

_log = logging.getLogger(__name__)


class MlxLmBackend:
    name = "llm"

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self.model = config.model
        self._model = None
        self._tokenizer = None

    def preflight(self) -> str | None:
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
        return mlx_load(self.model)

    def load(self) -> None:
        self._model, self._tokenizer = self._load_model()

    def unload(self) -> None:
        self._model = None
        self._tokenizer = None
        mx.clear_cache()

    def generate(self, system: str, prompt: str, max_tokens: int | None = None) -> str:
        model, tokenizer = self._require_loaded()
        rendered = self._render(system, prompt)
        text = mlx_generate(
            model,
            tokenizer,
            prompt=rendered,
            max_tokens=self._config.max_tokens if max_tokens is None else max_tokens,
            verbose=False,
        )
        return text.strip()

    def _require_loaded(self):
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("llm backend is not loaded")
        return self._model, self._tokenizer

    def _render(self, system: str, prompt: str) -> str:
        """system ロールを受け付けないテンプレートのために連結版へ退避する。

        enable_thinking=False は両方の経路で必須。既定では思考モデルが読み上げ用
        要約の前に英語の chain-of-thought を出力してしまい、それがそのまま
        read-aloud フックで音声として読み上げられる（フックの日本語判定はユーザー
        入力を引用した思考ブロックにも一致してしまうため見逃せない）。
        """
        _, tokenizer = self._require_loaded()
        try:
            return tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                add_generation_prompt=True,
                enable_thinking=False,
            )
        except Exception as error:
            _log.warning(
                "chat template rejected the system role; falling back to concatenation: %s",
                error,
            )
            return tokenizer.apply_chat_template(
                [{"role": "user", "content": f"{system}\n\n{prompt}"}],
                add_generation_prompt=True,
                enable_thinking=False,
            )
