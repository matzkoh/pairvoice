"""CoreAudio の公開 API で、音声入出力中のプロセスを調べる。

追加パッケージも Xcode も要らない。定数は AudioHardware.h の値。
"""

from __future__ import annotations

import ctypes
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_core_audio = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_libproc = ctypes.CDLL("/usr/lib/libproc.dylib")

_SYSTEM_OBJECT = 1
_PROC_PIDPATHINFO_MAXSIZE = 4096


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode())[0]


_SCOPE_GLOBAL = _fourcc("glob")
_PROCESS_LIST = _fourcc("prs#")
_PID = _fourcc("ppid")
_IS_RUNNING_INPUT = _fourcc("piri")
_IS_RUNNING_OUTPUT = _fourcc("piro")


class _Address(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class AudioProcess:
    pid: int
    name: str
    input_running: bool
    output_running: bool


def _get_property(obj: int, selector: int, ctype) -> list | None:
    address = _Address(selector, _SCOPE_GLOBAL, 0)
    size = ctypes.c_uint32(0)
    if (
        _core_audio.AudioObjectGetPropertyDataSize(
            ctypes.c_uint32(obj), ctypes.byref(address), 0, None, ctypes.byref(size)
        )
        != 0
    ):
        return None
    count = size.value // ctypes.sizeof(ctype)
    if count == 0:
        return []
    buffer = (ctype * count)()
    if (
        _core_audio.AudioObjectGetPropertyData(
            ctypes.c_uint32(obj),
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(buffer),
        )
        != 0
    ):
        return None
    return list(buffer)


def _process_name(pid: int, cache: dict[int, str]) -> str:
    cached = cache.get(pid)
    if cached is not None:
        return cached
    buffer = ctypes.create_string_buffer(_PROC_PIDPATHINFO_MAXSIZE)
    length = _libproc.proc_pidpath(pid, buffer, _PROC_PIDPATHINFO_MAXSIZE)
    name = Path(buffer.value.decode(errors="replace")).name if length > 0 else ""
    cache[pid] = name
    return name


def sample_processes() -> tuple[AudioProcess, ...]:
    # 呼び出しごとに作り直すローカルキャッシュ。プロセスは終了後に pid が
    # 再利用されるため、呼び出しをまたいで保持すると古い名前を返しかねない。
    name_cache: dict[int, str] = {}
    objects = _get_property(_SYSTEM_OBJECT, _PROCESS_LIST, ctypes.c_uint32) or []
    processes = []
    for obj in objects:
        pid_values = _get_property(obj, _PID, ctypes.c_int32)
        if not pid_values:
            continue
        pid = pid_values[0]
        input_values = _get_property(obj, _IS_RUNNING_INPUT, ctypes.c_uint32) or [0]
        output_values = _get_property(obj, _IS_RUNNING_OUTPUT, ctypes.c_uint32) or [0]
        output_running = bool(output_values[0])
        processes.append(
            AudioProcess(
                pid=pid,
                # 名前は出力の除外にしか使わないので、出力中のプロセスだけ引く。
                # 読み上げの間は 0.25 秒ごとに呼ばれる
                name=_process_name(pid, name_cache) if output_running else "",
                input_running=bool(input_values[0]),
                output_running=output_running,
            )
        )
    return tuple(processes)


@dataclass(frozen=True)
class AudioActivity:
    microphone: bool
    output: bool


class AudioProbe:
    """呼ばれた時点の入出力の有無を1回だけ取る（約13ms）。

    再生の直前に確かめたいので、裏で監視して結果を溜めることはしない。
    自分（常駐サーバー）の再生はほかのアプリの音に数えない。
    """

    def __init__(
        self,
        ignore_processes: tuple[str, ...] = (),
        sampler: Callable[[], tuple[AudioProcess, ...]] = sample_processes,
        own_pid: int | None = None,
    ) -> None:
        self._ignored = {name.lower() for name in ignore_processes}
        self._sampler = sampler
        self._own_pid = os.getpid() if own_pid is None else own_pid

    def sample(self) -> AudioActivity:
        processes = self._sampler()
        return AudioActivity(
            microphone=any(p.input_running for p in processes),
            output=any(
                p.output_running and p.pid != self._own_pid and p.name.lower() not in self._ignored
                for p in processes
            ),
        )
