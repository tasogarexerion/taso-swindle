from __future__ import annotations


def compute_mate_urgency(mate_score: int | None) -> float:
    if mate_score is None:
        return 0.0
    if mate_score > 0:
        return 1.0 / float(max(1, mate_score))
    return 0.0
