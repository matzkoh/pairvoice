"""モデルを載せずにライフサイクルとサーバーをテストするためのダミー実装。"""

from __future__ import annotations

import asyncio
import time

from pairvoice.audio_state import AudioActivity


class FakeBackend:
    def __init__(
        self,
        name="fake",
        model="fake/model",
        *,
        downloaded=True,
        fail_load=False,
        preflight_problem=None,
        load_delay=0.0,
        download_delay=0.0,
    ):
        self.name = name
        self.model = model
        self.downloaded = downloaded
        self.fail_load = fail_load
        self.preflight_problem = preflight_problem
        self.load_delay = load_delay
        self.download_delay = download_delay
        self.load_calls = 0
        self.unload_calls = 0
        self.download_calls = 0
        self.loaded = False

    def preflight(self):
        return self.preflight_problem

    def is_downloaded(self):
        return self.downloaded

    def download(self):
        self.download_calls += 1
        if self.download_delay:
            time.sleep(self.download_delay)
        self.downloaded = True

    def load(self):
        self.load_calls += 1
        if self.load_delay:
            time.sleep(self.load_delay)
        if self.fail_load:
            raise RuntimeError("load failed")
        self.loaded = True

    def unload(self):
        self.unload_calls += 1
        self.loaded = False


async def gather_results(coroutines):
    return await asyncio.gather(*coroutines, return_exceptions=True)


class FakeProbe:
    """AudioProbe の代わり。属性を書き換えると次の sample() から反映される。"""

    def __init__(self, microphone=False, output=False, error=None):
        self.microphone = microphone
        self.output = output
        self.error = error
        self.samples = 0

    def sample(self):
        self.samples += 1
        if self.error is not None:
            raise self.error
        return AudioActivity(
            microphone=("fake",) if self.microphone else (),
            output=("fake",) if self.output else (),
        )


class FakeCurate(FakeBackend):
    """音の大きさで話者を分ける（0.3 より大きい所は 0 番、それより小さい声は 1 番）。雑音は除かない。"""

    def __init__(self, **kwargs):
        super().__init__(name="curate", model="fake/curate", **kwargs)
        self.diarize_calls = 0
        self.enhance_calls = 0

    def diarize(self, chunk, state):
        import numpy as np

        from pairvoice.curate import DIARIZE_RATE, Segment

        self.diarize_calls += 1
        offset = state or 0.0
        hop = DIARIZE_RATE // 100
        frames = len(chunk) // hop
        peak = np.abs(chunk[: frames * hop]).reshape(frames, hop).max(axis=1)
        segments = []
        for speaker, voiced in ((0, peak > 0.3), (1, (peak > 0.05) & (peak <= 0.3))):
            edges = np.flatnonzero(np.diff(np.r_[0, voiced.astype(np.int8), 0])).reshape(-1, 2)
            segments += [Segment(offset + a / 100, offset + b / 100, speaker) for a, b in edges]
        return segments, offset + len(chunk) / DIARIZE_RATE

    def enhance(self, samples):
        self.enhance_calls += 1
        return samples * 0.999
