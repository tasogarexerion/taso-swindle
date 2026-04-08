from __future__ import annotations

from typing import Iterable, Optional

from ..candidate import CandidateMove
from ..reply_search import ReplyEval
from .onlymove_features import compute_onlymove_pressure


def compute_self_risk(
    candidate: CandidateMove,
    reply_topk: Optional[Iterable[ReplyEval]] = None,
) -> float:
    replies = list(reply_topk) if reply_topk is not None else list(getattr(candidate, "reply_topk", []) or [])

    risk = _base_risk(candidate)
    if not replies:
        return _clamp(risk)

    replies.sort(key=lambda r: r.opp_utility, reverse=True)
    top = replies[0]

    # Opponent immediate mate is critical danger.
    if top.mate_raw is not None and top.mate_raw > 0:
        return 1.0

    # If root-side eval after opponent best reply is very bad, raise risk.
    if top.root_cp is not None:
        if top.root_cp <= -2500:
            risk = max(risk, 0.9)
        elif top.root_cp <= -1500:
            risk = max(risk, 0.75)
        elif top.root_cp <= -700:
            risk = max(risk, 0.6)

    onlymove, gap12, _ = compute_onlymove_pressure(replies)
    if gap12 >= 300.0:
        # One difficult-only response tends to be lower practical risk vs human.
        risk -= min(0.15, 0.1 + onlymove * 0.1)

    # If multiple mate-ish responses exist, risk remains high.
    mate_positive = sum(1 for r in replies[:3] if r.mate_raw is not None and r.mate_raw > 0)
    if mate_positive >= 2:
        risk = max(risk, 0.95)

    return _clamp(risk)


def _base_risk(candidate: CandidateMove) -> float:
    if candidate.mate_score is not None and candidate.mate_score < 0:
        return 1.0
    if candidate.base_cp is None:
        return 0.5
    if candidate.base_cp <= -2500:
        return 0.8
    if candidate.base_cp <= -1500:
        return 0.6
    if candidate.base_cp <= -700:
        return 0.4
    return 0.2


def compute_survival_score(
    candidate: CandidateMove,
    reply_topk: Optional[Iterable[ReplyEval]] = None,
    *,
    pseudo_hisshi_score: float = 0.0,
) -> float:
    if candidate.mate_score is not None and candidate.mate_score > 0:
        return 1.0
    risk = compute_self_risk(candidate, reply_topk)
    bonus = max(0.0, min(1.0, pseudo_hisshi_score)) * 0.15
    return _clamp(1.0 - risk + bonus)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
