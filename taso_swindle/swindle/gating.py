from __future__ import annotations

from .candidate import CandidateMove
from ..usi_messages import is_usi_move_token


def apply_phase1_gate(
    candidate: CandidateMove,
    best_base_cp: int | None,
    drop_cap_cp: int,
    mate_priority: bool,
) -> None:
    if not is_usi_move_token(candidate.move):
        candidate.gate_rejected = True
        candidate.gate_reason = "invalid_move"
        return

    if candidate.mate_score is not None and candidate.mate_score < 0:
        candidate.gate_rejected = True
        candidate.gate_reason = "self_mate_risk"
        return

    if mate_priority and candidate.mate_score is not None and candidate.mate_score > 0:
        candidate.gate_rejected = False
        candidate.gate_reason = None
        return

    if best_base_cp is not None and candidate.base_cp is not None:
        if candidate.base_cp < best_base_cp - drop_cap_cp:
            candidate.gate_rejected = True
            candidate.gate_reason = "eval_drop_cap"
            return

    if candidate.features.threat_score < 0.05 and candidate.features.mate_urgency <= 0.0:
        if candidate.base_cp is not None and best_base_cp is not None:
            if candidate.base_cp < best_base_cp - int(drop_cap_cp * 0.5):
                candidate.gate_rejected = True
                candidate.gate_reason = "low_pressure"
                return

    candidate.gate_rejected = False
    candidate.gate_reason = None
