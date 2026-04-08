from __future__ import annotations

from typing import Iterable

from ..reply_search import ReplyEval


def compute_human_trap_score(
    reply_topk: Iterable[ReplyEval] | None = None,
    *,
    gap12: float = 0.0,
    gap13: float = 0.0,
) -> float:
    """
    Human trap score (heuristic).

    - flashy-trap
    - quiet-only-move
    - mislead-collapse
    """
    replies = sorted(list(reply_topk or []), key=lambda r: r.opp_utility, reverse=True)
    if not replies:
        return 0.0

    top = replies[0]
    score = 0.0

    # 1) flashy-trap: natural flashy response exists but has huge gap to best.
    if top.is_flashy_like and gap12 >= 350.0:
        score += 0.35

    # 2) quiet-only-move: large gap and top move does not look forcing.
    if (not top.is_check_like) and (not top.is_flashy_like) and gap12 >= 280.0:
        score += 0.35

    # 3) mislead-collapse: reply2+ utility falls sharply.
    if len(replies) >= 3:
        u2 = replies[1].opp_utility
        u3 = replies[2].opp_utility
        collapse = max(0.0, u2 - u3)
        if collapse >= 200.0:
            score += min(0.25, collapse / 1600.0)
    elif gap13 > gap12 and gap13 >= 400.0:
        score += 0.12

    # Non-zero baseline when reply data exists.
    if score <= 0.0:
        score = 0.02

    return _clamp(score)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
