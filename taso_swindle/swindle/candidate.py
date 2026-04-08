from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .reply_search import ReplyEval


@dataclass
class RevBreakdown:
    mate: float = 0.0
    threat: float = 0.0
    onlymove: float = 0.0
    entropy: float = 0.0
    trap: float = 0.0
    risk_penalty: float = 0.0
    survival: float = 0.0
    pseudo_hisshi_bonus: float = 0.0
    total: float = 0.0


@dataclass
class SwindleFeatures:
    mate_urgency: float = 0.0
    threat_score: float = 0.0
    only_move_pressure: float = 0.0
    reply_entropy_score: float = 0.0
    human_trap_score: float = 0.0
    self_risk: float = 0.0
    survival_score: float = 0.0
    gap12: float = 0.0
    gap13: float = 0.0
    reply_entropy: float = 0.0
    pseudo_hisshi_score: float = 0.0
    mate_chance: float = 0.0


@dataclass
class CandidateMove:
    move: str
    pv: list[str]
    base_cp: Optional[int]
    mate_score: Optional[int]
    depth: int = 0
    seldepth: Optional[int] = None
    nodes: Optional[int] = None
    nps: Optional[int] = None
    hashfull: Optional[int] = None
    gives_check: bool = False
    features: SwindleFeatures = field(default_factory=SwindleFeatures)
    rev_score: float = 0.0
    rev_breakdown: RevBreakdown = field(default_factory=RevBreakdown)
    gate_rejected: bool = False
    gate_reason: Optional[str] = None
    multipv: int = 1
    reply_topk: list["ReplyEval"] = field(default_factory=list)
    mate_verify_status: Optional[str] = None

    def score_for_display(self) -> str:
        if self.mate_score is not None:
            return f"mate {self.mate_score:+d}"
        if self.base_cp is not None:
            return f"cp {self.base_cp:+d}"
        return "unknown"
