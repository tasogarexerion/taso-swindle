from __future__ import annotations

import math
from typing import Iterable

from ..reply_search import ReplyEval


def compute_onlymove_pressure(
    reply_topk: Iterable[ReplyEval] | None = None,
) -> tuple[float, float, float]:
    """
    Compute only-move pressure from opponent reply evaluations.

    Returns:
      (only_move_pressure, gap12, gap13)
    """
    replies = list(reply_topk or [])
    if not replies:
        return 0.0, 0.0, 0.0

    sorted_replies = sorted(replies, key=lambda r: r.opp_utility, reverse=True)
    u1 = sorted_replies[0].opp_utility
    u2 = sorted_replies[1].opp_utility if len(sorted_replies) >= 2 else None
    u3 = sorted_replies[2].opp_utility if len(sorted_replies) >= 3 else None

    gap12 = max(0.0, u1 - u2) if u2 is not None else 0.0
    if u3 is not None:
        gap13 = max(0.0, u1 - u3)
    elif u2 is not None:
        gap13 = gap12 * 0.5
    else:
        gap13 = 0.0

    # mate-aware boost: if top reply is forced mate-ish but alternatives are not, pressure is high.
    mate_boost = 0.0
    top = sorted_replies[0]
    if top.mate_raw is not None and top.mate_raw > 0:
        second_mate = sorted_replies[1].mate_raw if len(sorted_replies) >= 2 else None
        if second_mate is None or second_mate <= 0:
            mate_boost = 0.35

    n12 = _norm_gap(gap12)
    n13 = _norm_gap(gap13)
    pressure = 0.65 * n12 + 0.35 * n13 + mate_boost
    return _clamp(pressure), gap12, gap13


def _norm_gap(gap: float) -> float:
    if not math.isfinite(gap) or gap <= 0.0:
        return 0.0
    # log scale keeps large cp spikes bounded.
    return _clamp(math.log1p(gap) / math.log1p(2500.0))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
