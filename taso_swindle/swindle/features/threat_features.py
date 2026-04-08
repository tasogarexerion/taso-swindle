from __future__ import annotations

from ..candidate import CandidateMove


def compute_threat_score(candidate: CandidateMove) -> float:
    if candidate.mate_score is not None and candidate.mate_score > 0:
        return 1.0

    score = 0.05
    move = candidate.move

    if "*" in move and move[0].upper() in {"R", "B", "G", "S"}:
        score += 0.35
    if move.endswith("+"):
        score += 0.20
    if candidate.depth >= 12:
        score += 0.15
    if candidate.base_cp is not None and candidate.base_cp > 0:
        score += 0.20

    return max(0.0, min(1.0, score))
