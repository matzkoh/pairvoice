"""PyTorch の重み（`torch.save` の zip）を、torch を入れずに numpy の配列として読む。

pickle は任意のコードを動かせるので、テンソルの組み立てに要る名前だけを通し、ほかの名前が
出てきたら読むのをやめる（torch.load の weights_only=True と同じ考え方）。
"""

from __future__ import annotations

import pickle
import zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np

_STORAGE_DTYPES = {
    "FloatStorage": np.float32,
    "HalfStorage": np.float16,
    "DoubleStorage": np.float64,
    "LongStorage": np.int64,
    "IntStorage": np.int32,
    "ShortStorage": np.int16,
    "CharStorage": np.int8,
    "ByteStorage": np.uint8,
    "BoolStorage": np.bool_,
}


class UnsafeCheckpoint(Exception):
    """テンソル以外のものを組み立てようとする重み。"""


def _rebuild_tensor(storage, offset, size, stride, *_):
    # as_strided は範囲を確かめないので、ストレージの外を指す形はここで断る
    if len(size) != len(stride):
        raise UnsafeCheckpoint("テンソルの形と歩幅の次元が合いません")
    last = offset + sum((n - 1) * s for n, s in zip(size, stride, strict=True) if n > 0)
    if offset < 0 or min(stride, default=0) < 0 or (0 not in size and last >= len(storage)):
        raise UnsafeCheckpoint("テンソルの形がストレージに収まりません")
    if not size:
        return storage[offset : offset + 1].reshape(())
    itemsize = storage.itemsize
    return np.lib.stride_tricks.as_strided(
        storage[offset:], shape=tuple(size), strides=tuple(s * itemsize for s in stride)
    ).copy()


class _Unpickler(pickle.Unpickler):
    def __init__(self, file, archive: zipfile.ZipFile, root: str) -> None:
        super().__init__(file)
        self._archive = archive
        self._root = root

    def find_class(self, module: str, name: str):
        if (module, name) == ("collections", "OrderedDict"):
            return OrderedDict
        if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
            return _rebuild_tensor
        if module == "torch" and name in _STORAGE_DTYPES:
            return _STORAGE_DTYPES[name]
        raise UnsafeCheckpoint(f"{module}.{name} は読みません")

    def persistent_load(self, pid):
        kind, dtype, key, _location, _numel = pid
        if kind != "storage" or dtype not in _STORAGE_DTYPES.values():
            raise UnsafeCheckpoint(f"{kind} は読みません")
        data = self._archive.read(f"{self._root}/data/{key}")
        return np.frombuffer(data, dtype=dtype)


def load(path: Path) -> dict[str, np.ndarray]:
    """名前とテンソルの辞書。Lightning などの `state_dict` の入れ子もほどく。"""
    with zipfile.ZipFile(path) as archive:
        pkl = next(n for n in archive.namelist() if n.endswith("/data.pkl"))
        root = pkl[: -len("/data.pkl")]
        if archive.read(f"{root}/byteorder").strip() not in (b"", b"little"):
            raise UnsafeCheckpoint("ビッグエンディアンの重みは読みません")
        with archive.open(pkl) as file:
            obj = _Unpickler(file, archive, root).load()
    if isinstance(obj, dict) and isinstance(obj.get("state_dict"), dict):
        obj = obj["state_dict"]
    if not isinstance(obj, dict):
        raise UnsafeCheckpoint("名前とテンソルの辞書ではありません")
    return {k: v for k, v in obj.items() if isinstance(v, np.ndarray)}
