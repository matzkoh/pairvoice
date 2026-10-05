"""モデルを載せずにライフサイクルとサーバーをテストするためのダミー実装。"""

from __future__ import annotations

import asyncio

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
    ):
        self.name = name
        self.model = model
        self.downloaded = downloaded
        self.fail_load = fail_load
        self.preflight_problem = preflight_problem
        self.load_delay = load_delay
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
        self.downloaded = True

    def load(self):
        self.load_calls += 1
        if self.load_delay:
            import time

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
        return AudioActivity(microphone=self.microphone, output=self.output)
