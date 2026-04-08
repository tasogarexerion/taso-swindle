from __future__ import annotations

from typing import Dict

from .candidate import CandidateMove
from .context import SwindleContext

MODE_AUTO = "AUTO"
MODE_TACTICAL = "TACTICAL"
MODE_MURKY = "MURKY"
MODE_HYBRID = "HYBRID"


def resolve_mode(
    requested_mode: str,
    context: SwindleContext,
    candidates: list[CandidateMove] | None = None,
    entropy_hint: float | None = None,
) -> str:
    mode = requested_mode.strip().upper()
    if mode in {MODE_TACTICAL, MODE_MURKY, MODE_HYBRID}:
        return mode

    if mode != MODE_AUTO:
        return MODE_HYBRID

    if context.root_mate_score is not None:
        return MODE_TACTICAL

    cands = list(candidates or [])
    if cands and _tactical_signal(cands):
        return MODE_TACTICAL

    if entropy_hint is not None and entropy_hint >= 0.62:
        return MODE_MURKY

    if cands and _murky_signal(cands):
        return MODE_MURKY

    return MODE_HYBRID


def mode_weight_scale(mode: str) -> Dict[str, float]:
    if mode == MODE_TACTICAL:
        return {
            "mate": 1.15,
            "threat": 1.15,
            "onlymove": 1.10,
            "entropy": 0.85,
            "trap": 0.85,
            "risk": 1.00,
            "survival": 1.05,
            "pseudo_hisshi": 1.10,
        }

    if mode == MODE_MURKY:
        return {
            "mate": 1.00,
            "threat": 0.90,
            "onlymove": 1.00,
            "entropy": 1.25,
            "trap": 1.25,
            "risk": 1.00,
            "survival": 1.00,
            "pseudo_hisshi": 0.90,
        }

    return {
        "mate": 1.00,
        "threat": 1.00,
        "onlymove": 1.00,
        "entropy": 1.00,
        "trap": 1.00,
        "risk": 1.00,
        "survival": 1.00,
        "pseudo_hisshi": 1.00,
    }


def _tactical_signal(cands: list[CandidateMove]) -> bool:
    if not cands:
        return False
    check_like = 0
    for c in cands[:6]:
        mv = c.move.strip()
        if mv.endswith("+") or mv.startswith(("R*", "B*")):
            check_like += 1
    return check_like >= max(2, len(cands[:6]) // 2)


def _murky_signal(cands: list[CandidateMove]) -> bool:
    cps = [c.base_cp for c in cands if c.base_cp is not None and c.mate_score is None]
    if len(cps) < 3:
        return False
    cps.sort(reverse=True)
    spread = cps[0] - cps[min(2, len(cps) - 1)]
    return spread <= 220
