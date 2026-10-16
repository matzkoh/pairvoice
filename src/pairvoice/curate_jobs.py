"""studio で区間を選ぶ間、解析した素材を持っておく。

素材のデコードと話者分離は長い素材で数分かかるので、選び直すたびにやり直さない。
デコードした素材は一時ディレクトリに置き、最後に触ってから JOB_TTL_SECONDS か、
MAX_JOBS を超えたら古い順に消す（常駐サーバーを止めれば OS の一時ディレクトリに残る分も消える）。
"""

from __future__ import annotations

import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass

from .curate import Analysis

JOB_TTL_SECONDS = 3600.0
# 48kHz のステレオで1時間あたり約 0.7GB を抱える
MAX_JOBS = 3


class CurateJobNotFound(Exception):
    """解析が無い（消えた・常駐サーバーを再起動した）。"""


@dataclass
class _Job:
    analysis: Analysis
    directory: tempfile.TemporaryDirectory
    touched: float


class CurateJobs:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._jobs: dict[str, _Job] = {}

    def add(self, analysis: Analysis, directory: tempfile.TemporaryDirectory) -> str:
        self._sweep()
        while len(self._jobs) >= MAX_JOBS:
            oldest = min(self._jobs, key=lambda k: self._jobs[k].touched)
            self.remove(oldest)
        job_id = secrets.token_hex(8)
        self._jobs[job_id] = _Job(analysis, directory, self._clock())
        return job_id

    def get(self, job_id: str) -> Analysis:
        self._sweep()
        job = self._jobs.get(job_id)
        if job is None:
            raise CurateJobNotFound(job_id)
        job.touched = self._clock()
        return job.analysis

    def remove(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job is None:
            raise CurateJobNotFound(job_id)
        job.directory.cleanup()

    def _sweep(self) -> None:
        now = self._clock()
        for job_id in [k for k, j in self._jobs.items() if now - j.touched > JOB_TTL_SECONDS]:
            self.remove(job_id)
