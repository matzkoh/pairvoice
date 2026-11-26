"""こもった声を直す量の探索の部品。少ない量から順に試し、目標に届いた最初の量で止める。"""

from itertools import pairwise

from pairvoice.unmuffle import AMOUNTS, TARGET_DB, is_clear


def clear_exactly_at_or_above_target(worst_db: float, measured: bool) -> bool:
    """
    post: __return__
    """
    worst = worst_db if measured else None
    return is_clear(worst) == (worst is None or worst >= TARGET_DB)


def amounts_rise_from_nothing() -> bool:
    """足さない量から始まり、昇順に試す（量と効きは単調でないので二分探索しない）。

    post: __return__
    """
    return AMOUNTS[0] == 0 and all(a < b for a, b in pairwise(AMOUNTS))
