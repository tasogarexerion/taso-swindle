from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class DecisionCandidateRecord:
    move: str
    base_cp: Optional[int]
    mate: Optional[int]
    depth: int
    features: Dict[str, Any]
    rev_score: float = 0.0
    gate_rejected: bool = False
    gate_reason: Optional[str] = None
    reply_topk: list[Dict[str, Any]] = field(default_factory=list)
    gap12: Optional[float] = None
    gap13: Optional[float] = None
    reply_entropy: Optional[float] = None
    pseudo_hisshi_score: Optional[float] = None
    rev_breakdown: Dict[str, Any] = field(default_factory=dict)
    mate_verify_status: Optional[str] = None


@dataclass
class DecisionEvent:
    timestamp: str
    game_id: str
    ply: int
    root_sfen: str
    root_eval_cp: Optional[int]
    root_mate: Optional[int]
    swindle_enabled: bool
    mode: str
    time_info: Dict[str, Any]
    normal_bestmove: str
    final_bestmove: str
    candidates: list[DecisionCandidateRecord] = field(default_factory=list)
    selected_reason: str = ""
    backend_engine_info: Dict[str, Any] = field(default_factory=dict)
    emergency_fast_mode: bool = False
    events: list[str] = field(default_factory=list)
    option_restore_failed: bool = False
    mate_verify_status: str = "not_used"
    verify_status_summary: str = "not_used"
    verify_mode_used: str = "VERIFY_ONLY"
    verify_engine_kind: str = "backend"
    mate_verify_candidates_count: int = 0
    dfpn_used: bool = False
    dfpn_status_summary: str = "not_used"
    dfpn_parser_hits: list[str] = field(default_factory=list)
    dfpn_parse_unknown_count: int = 0
    dfpn_distance_available_count: int = 0
    dfpn_dialect_used: str = "none"
    dfpn_dialect_candidates: list[str] = field(default_factory=list)
    dfpn_source_detail_normalized: str = "none"
    dfpn_pack_source: str = "builtin"
    dfpn_pack_version: str = "unknown"
    dfpn_pack_load_errors: int = 0
    verify_conflict_count: int = 0
    verify_unknown_count: int = 0
    dfpn_parser_mode: str = "AUTO"
    verify_hybrid_policy: str = "CONSERVATIVE"
    hybrid_learned_adjustment_used: bool = False
    hybrid_adjustment_delta: float = 0.0
    hybrid_adjustment_source: str = "none"
    pseudo_hisshi_status: str = "not_used"
    ponder_status_summary: str = "not_used"
    ponder_cache_used: bool = False
    ponder_cache_hit: bool = False
    ponder_used_budget_ms: int = 0
    ponder_fallback_reason: Optional[str] = None
    ponder_reuse_score: Optional[float] = None
    ponder_cache_age_ms: int = 0
    ponder_cache_gate_reason: Optional[str] = None
    ponder_gate_learned_adjustment_used: bool = False
    ponder_gate_adjustment_delta: float = 0.0
    ponder_gate_adjustment_source: str = "none"
    reuse_then_bestmove_changed: bool = False
    ponder_reuse_decision_id: Optional[str] = None
    ponder_reuse_parent_position_key: Optional[str] = None
    ponder_label_source: str = "heuristic"
    ponder_label_confidence: float = 0.0
    backend_restart_count: int = 0
    dfpn_status: str = "none"
    actual_opponent_move: Optional[str] = None
    actual_move_in_reply_topk: Optional[bool] = None
    actual_move_rank_in_reply_topk: Optional[int] = None
    outcome_tag: Optional[str] = None
    outcome_confidence: Optional[float] = None
    dry_run: bool = False
    search_id: int = 0
