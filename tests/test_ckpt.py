import io
import pickle
import sys
import types
import zipfile
from collections import OrderedDict

import numpy as np
import pytest

from pairvoice import ckpt


class _Storage:
    def __init__(self, key, data):
        self.key, self.data = key, data


class _Tensor:
    def __init__(self, storage, offset, size, stride):
        self.args = (storage, offset, size, stride, False, OrderedDict())

    def __reduce__(self):
        return (sys.modules["torch._utils"]._rebuild_tensor_v2, self.args)


class _Pickler(pickle.Pickler):
    def persistent_id(self, obj):
        if isinstance(obj, _Storage):
            return ("storage", sys.modules["torch"].FloatStorage, obj.key, "cpu", obj.data.size)
        return None


@pytest.fixture
def fake_torch(monkeypatch):
    """pickle が torch の名前を書けるように、同じ名前の空の入れ物を置く（torch は入れない）。"""
    torch = types.ModuleType("torch")
    utils = types.ModuleType("torch._utils")

    class FloatStorage:
        pass

    def _rebuild_tensor_v2(*args):
        raise AssertionError("書くときには呼ばれない")

    FloatStorage.__module__ = "torch"
    FloatStorage.__qualname__ = "FloatStorage"
    _rebuild_tensor_v2.__module__ = "torch._utils"
    _rebuild_tensor_v2.__qualname__ = "_rebuild_tensor_v2"
    vars(torch).update(FloatStorage=FloatStorage)
    vars(utils).update(_rebuild_tensor_v2=_rebuild_tensor_v2)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch._utils", utils)


def write_checkpoint(path, obj, storages):
    buffer = io.BytesIO()
    _Pickler(buffer, protocol=2).dump(obj)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("archive/data.pkl", buffer.getvalue())
        archive.writestr("archive/byteorder", "little")
        for storage in storages:
            archive.writestr(f"archive/data/{storage.key}", storage.data.tobytes())


def test_load_reads_tensors_with_strides_and_state_dict(fake_torch, tmp_path):
    data = np.arange(12, dtype=np.float32)
    storage = _Storage("0", data)
    state = OrderedDict(
        [
            ("a.weight", _Tensor(storage, 0, (3, 4), (4, 1))),
            # 転置（列の順に並ぶ）と、途中から始まる1次元
            ("b.weight", _Tensor(storage, 0, (4, 3), (1, 4))),
            ("c.bias", _Tensor(storage, 2, (5,), (1,))),
        ]
    )
    path = tmp_path / "model.ckpt"
    write_checkpoint(path, {"state_dict": state, "epoch": 3}, [storage])

    loaded = ckpt.load(path)

    assert list(loaded) == ["a.weight", "b.weight", "c.bias"]
    np.testing.assert_array_equal(loaded["a.weight"], data.reshape(3, 4))
    np.testing.assert_array_equal(loaded["b.weight"], data.reshape(3, 4).T)
    np.testing.assert_array_equal(loaded["c.bias"], data[2:7])


def test_load_refuses_a_shape_outside_the_storage(fake_torch, tmp_path):
    storage = _Storage("0", np.zeros(4, np.float32))
    path = tmp_path / "model.ckpt"
    write_checkpoint(path, {"w": _Tensor(storage, 0, (100,), (1,))}, [storage])

    with pytest.raises(ckpt.UnsafeCheckpoint):
        ckpt.load(path)


class _Exploit:
    def __reduce__(self):
        import os

        return (os.system, ("echo pwned",))


def test_load_refuses_other_globals(tmp_path):
    path = tmp_path / "model.ckpt"
    write_checkpoint(path, {"w": _Exploit()}, [])

    with pytest.raises(ckpt.UnsafeCheckpoint):
        ckpt.load(path)
