from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MateResult:
    found_mate: bool
    mate_in: Optional[int] = None
    distance: Optional[int] = None
    confidence: float = 0.0
    source: str = "none"
    status: str = "not_used"
    engine_kind: str = "backend"
    mate_sign: Optional[str] = None
    source_detail: Optional[str] = None
    raw_summary: Optional[str] = None
    dfpn_dialect_used: Optional[str] = None
    dfpn_dialect_candidates: list[str] = field(default_factory=list)
    dfpn_source_detail_normalized: Optional[str] = None
    dfpn_pack_source: Optional[str] = None
    dfpn_pack_version: Optional[str] = None
    dfpn_pack_load_errors: int = 0
    hybrid_learned_adjustment_used: bool = False
    hybrid_adjustment_delta: float = 0.0
    hybrid_adjustment_source: str = "none"
    notes: list[str] = field(default_factory=list)
