"""合成した wav（tts.output_dir）の掃除。

合成のたびに新しい wav が増え、消す者がいないので、作ってから日数が経ったものを消す。
studio のレビューの再生はファイルが無ければ 404 にするだけなので、古いレビューの音声は
聞けなくなる。試聴のテイクからプロファイルを作るのは試聴の直後なので困らない。
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

_log = logging.getLogger(__name__)

SECONDS_PER_DAY = 86400
# tts.speak() が付ける名前（uuid4）。output_dir を取り違えても利用者の wav は消さない
_GENERATED_NAME = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.wav"
)


def prune(directory: Path, max_age_days: int, *, now: float | None = None) -> int:
    """directory 直下の合成した wav のうち、更新から max_age_days 日を超えたものを消し、消した数を返す。

    max_age_days が 0 以下なら何も消さない。
    """
    if max_age_days <= 0:
        return 0
    cutoff = (time.time() if now is None else now) - max_age_days * SECONDS_PER_DAY
    removed = 0
    for path in directory.glob("*.wav"):
        if not _GENERATED_NAME.fullmatch(path.name):
            continue
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
        except OSError as failed:
            # 1件の失敗で残りを消し損ねると、溜まり続ける
            _log.warning("合成音声を消せませんでした: %s", failed)
    if removed:
        _log.info("%d 日を過ぎた合成音声を %d 件消しました", max_age_days, removed)
    return removed
