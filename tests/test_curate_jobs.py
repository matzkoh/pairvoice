import tempfile
from pathlib import Path
from typing import cast

import pytest

from pairvoice.curate import Analysis
from pairvoice.curate_jobs import JOB_TTL_SECONDS, MAX_JOBS, CurateJobNotFound, CurateJobs


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_jobs_expire_after_idle_and_drop_the_oldest_beyond_the_limit():
    clock = Clock()
    jobs = CurateJobs(clock)
    directories = [tempfile.TemporaryDirectory() for _ in range(MAX_JOBS + 1)]
    # 中身は見ないので、区別できる値ならよい
    analyses = [cast(Analysis, object()) for _ in directories]
    ids = []
    for k, directory in enumerate(directories[:MAX_JOBS]):
        clock.now = k
        ids.append(jobs.add(analyses[k], directory))
    # 使うと古さが戻る
    clock.now = 10
    assert jobs.get(ids[0]) is analyses[0]

    newest = jobs.add(analyses[-1], directories[-1])

    with pytest.raises(CurateJobNotFound):
        jobs.get(ids[1])
    assert jobs.get(ids[0]) is analyses[0]
    clock.now = 10 + JOB_TTL_SECONDS + 1
    with pytest.raises(CurateJobNotFound):
        jobs.get(newest)
    for directory in directories:
        assert not Path(directory.name).exists()
