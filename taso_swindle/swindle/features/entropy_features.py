from __future__ import annotations

import math
from typing import Iterable

from ..reply_search import ReplyEval


def compute_reply_entropy(
    reply_topk: Iterable[ReplyEval] | None = None,
    *,
    temperature: float = 350.0,
) -> tuple[float, float]:
    """
    Compute normalized entropy from opponent reply utilities.

    Returns:
      (reply_entropy_score, reply_entropy_normalized)
    """
    replies = list(reply_topk or [])
    if len(replies) <= 1:
        return 0.0, 0.0

    temp = max(1e-6, float(temperature))
    utilities = [float(r.opp_utility) for r in replies if math.isfinite(float(r.opp_utility))]
    if len(utilities) <= 1:
        return 0.0, 0.0

    u_max = max(utilities)
    exps = []
    for u in utilities:
        x = (u - u_max) / temp
        x = max(-80.0, min(80.0, x))
        exps.append(math.exp(x))

    z = sum(exps)
    if not math.isfinite(z) or z <= 0.0:
        return 0.0, 0.0

    probs = [v / z for v in exps]
    eps = 1e-12
    entropy = -sum(p * math.log(max(eps, p)) for p in probs)
    hmax = math.log(len(probs))
    if hmax <= 0.0:
        return 0.0, 0.0

    h_norm = entropy / hmax
    if not math.isfinite(h_norm):
        return 0.0, 0.0

    h_norm = _clamp(h_norm)
    return h_norm, h_norm


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
