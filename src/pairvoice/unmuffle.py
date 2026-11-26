"""こもった声を直す強さ。

caption だけで作った声や、それを混ぜた声は、10〜12kHz より上が欠けた、こもった音になることが
ある。上の帯域と張り（2〜5kHz）を戻す向きを話者の表現に足すと、同じ人の声のまま澄む（向きは muffle/<モデル>.npy。
scripts/muffle_direction.py が作る）。

どれだけ足せば澄んで聞こえるかは、測った帯域では決められなかった（もともと高音の少ない声ほど
届かないと出て、10kHz から切れた声は直ったと出た）。強さは利用者が前後を聴いて選ぶ。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

# 強さの上限。これより足すと 2〜4kHz の張りが削れ、声も変わる
MAX_AMOUNT = 4.0

UnmuffleAmount = Annotated[float, Field(ge=0, le=MAX_AMOUNT)]
