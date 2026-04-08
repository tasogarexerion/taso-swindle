from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from ..config import SwindleConfig
from .candidate import CandidateMove, RevBreakdown


@dataclass
class RevWeights:
    mate: float
    threat: float
    onlymove: float
    entropy: float
    trap: float
    risk: float
    survival: float
    pseudo_hisshi: float

    @classmethod
    def from_config(cls, config: SwindleConfig, scale: Dict[str, float]) -> "RevWeights":
        pseudo_hisshi_base = max(20.0, float(config.weight_threat) * 0.35)
        return cls(
            mate=float(config.weight_mate_urgency) * scale["mate"],
            threat=float(config.weight_threat) * scale["threat"],
            onlymove=float(config.weight_onlymove) * scale["onlymove"],
            entropy=float(config.weight_reply_entropy) * scale["entropy"],
            trap=float(config.weight_human_trap) * scale["trap"],
            risk=float(config.weight_self_risk) * scale["risk"],
            survival=float(config.weight_survival) * scale["survival"],
            pseudo_hisshi=pseudo_hisshi_base * scale.get("pseudo_hisshi", 1.0),
        )


def compute_rev_score(candidate: CandidateMove, weights: RevWeights) -> tuple[float, RevBreakdown]:
    f = candidate.features
    mate_term = weights.mate * f.mate_urgency
    threat_term = weights.threat * f.threat_score
    onlymove_term = weights.onlymove * f.only_move_pressure
    entropy_term = weights.entropy * f.reply_entropy_score
    trap_term = weights.trap * f.human_trap_score
    risk_penalty = weights.risk * f.self_risk
    survival_term = weights.survival * f.survival_score
    pseudo_hisshi_bonus = weights.pseudo_hisshi * f.pseudo_hisshi_score

    total = (
        mate_term
        + threat_term
        + onlymove_term
        + entropy_term
        + trap_term
        - risk_penalty
        + survival_term
        + pseudo_hisshi_bonus
    )
    breakdown = RevBreakdown(
        mate=mate_term,
        threat=threat_term,
        onlymove=onlymove_term,
        entropy=entropy_term,
        trap=trap_term,
        risk_penalty=risk_penalty,
        survival=survival_term,
        pseudo_hisshi_bonus=pseudo_hisshi_bonus,
        total=total,
    )
    return total, breakdown
