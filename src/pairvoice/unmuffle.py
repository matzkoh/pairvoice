"""こもった声を直す強さ。

caption だけで作った声や、それを混ぜた声は、10〜12kHz より上が欠け、低音が膨らんだ、こもった音に
なることがある。上の帯域を戻す向き（high）を足し、低音の膨らみの向き（boom）を引くと、同じ人の
声のまま澄む（向きは muffle/<モデル>.npz。scripts/muffle_direction.py が作る）。

どれだけ足せば澄んで聞こえるかは、測った帯域では決められなかった（もともと高音の少ない声ほど
届かないと出て、10kHz から切れた声は直ったと出た）。強さは利用者が前後を聴いて選ぶ。
"""

from __future__ import annotations

from typing import Annotated

import numpy as np
from pydantic import Field

# high を 1 足すごとに boom を引く量。耳で選んだ組み合わせ（high 2.5、boom 3.0）の比
BOOM_RATIO = 1.2
# 強さの上限。これより足すと声が変わり、読み崩れも出始める
MAX_AMOUNT = 3.0

UnmuffleAmount = Annotated[float, Field(ge=0, le=MAX_AMOUNT)]


def unmuffle_direction(high: np.ndarray, boom: np.ndarray) -> np.ndarray:
    """強さ 1 あたりに話者の表現へ足す向き。"""
    return (high - BOOM_RATIO * boom).astype(np.float32)
