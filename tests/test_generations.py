import os
import time
import uuid
from pathlib import Path

from pairvoice.generations import prune

DAY = 86400


def _touch(path, age_days, now):
    path.write_bytes(b"")
    stamp = now - age_days * DAY
    os.utime(path, (stamp, stamp))


def _generated():
    return f"{uuid.uuid4()}.wav"


def test_prune_removes_only_old_wavs(tmp_path):
    now = time.time()
    old, fresh = _generated(), _generated()
    _touch(tmp_path / old, 8, now)
    _touch(tmp_path / fresh, 6, now)
    _touch(tmp_path / "old.txt", 30, now)

    assert prune(tmp_path, 7, now=now) == 1

    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([fresh, "old.txt"])


def test_prune_leaves_wavs_it_did_not_generate(tmp_path):
    # output_dir を取り違えても、利用者の wav は消さない
    now = time.time()
    _touch(tmp_path / "voice.wav", 30, now)

    assert prune(tmp_path, 7, now=now) == 0
    assert (tmp_path / "voice.wav").exists()


def test_prune_skips_entries_it_cannot_remove_and_continues(tmp_path, monkeypatch):
    now = time.time()
    directory = tmp_path / _generated()
    directory.mkdir()
    stamp = now - 30 * DAY
    os.utime(directory, (stamp, stamp))
    blocked, old = _generated(), _generated()
    _touch(tmp_path / blocked, 30, now)
    _touch(tmp_path / old, 30, now)
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == blocked:
            raise PermissionError(self)
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert prune(tmp_path, 7, now=now) == 1
    assert directory.is_dir()
    assert (tmp_path / blocked).exists()
    assert not (tmp_path / old).exists()


def test_prune_keeps_everything_when_disabled(tmp_path):
    now = time.time()
    old = _generated()
    _touch(tmp_path / old, 365, now)

    assert prune(tmp_path, 0, now=now) == 0
    assert (tmp_path / old).exists()


def test_prune_tolerates_missing_directory(tmp_path):
    assert prune(tmp_path / "missing", 7) == 0
