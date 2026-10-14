"""Hugging Face のモデルの取得。どのバックエンドも同じ手順で落とす。"""

from __future__ import annotations


def is_downloaded(model: str) -> bool:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        snapshot_download(model, local_files_only=True)
    except (LocalEntryNotFoundError, OSError):
        return False
    return True


def download(model: str) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(model)
