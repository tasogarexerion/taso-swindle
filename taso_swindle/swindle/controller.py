from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import SwindleConfig
from ..info_parser import InfoParseResult, InfoSnapshot
from ..mate import MateAdapter
from .candidate import CandidateMove
from .context import SwindleContext
from .features import (
    compute_human_trap_score,
    compute_mate_urgency,
    compute_onlymove_pressure,
    compute_reply_entropy,
    compute_self_risk,
    compute_survival_score,
    compute_threat_score,
)
from .gating import apply_phase1_gate
from .modes import mode_weight_scale, resolve_mode
from .pseudo_hisshi import PseudoHisshiEstimator
from .reply_search import ProbeOutcome, ReplySearch, ReplySearchResult
from .scoring import RevWeights, compute_rev_score
from .weight_tuner import WeightTuner


ProbeRunner = Callable[[str, str], ProbeOutcome]
OptionSetter = Callable[[str, str], None]


@dataclass
class Stage1Decision:
    normal_bestmove: str
    selected_move: str
    selected_reason: str
    candidates: list[CandidateMove] = field(default_factory=list)
    mate_detected: bool = False
    activated: bool = False
    mode: str = "HYBRID"
    mode_requested: str = "HYBRID"
    events: list[str] = field(default_factory=list)
    option_restore_failed: bool = False
    deferred_commands: list[str] = field(default_factory=list)
    quit_requested: bool = False
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


@dataclass
class VerifySummary:
    status_summary: str = "not_used"
    verify_mode_used: str = "VERIFY_ONLY"
    verify_engine_kind: str = "backend"
    candidates_count: int = 0
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
    conflict_count: int = 0
    unknown_count: int = 0
    hybrid_adjustment_used: bool = False
    hybrid_adjustment_delta: float = 0.0
    hybrid_adjustment_source: str = "none"


class SwindleController:
    def __init__(
        self,
        config: SwindleConfig,
        pseudo_hisshi: Optional[PseudoHisshiEstimator] = None,
        weight_tuner: Optional[WeightTuner] = None,
    ) -> None:
        self.config = config
        self.pseudo_hisshi = pseudo_hisshi or PseudoHisshiEstimator()
        self.weight_tuner = weight_tuner or WeightTuner()

    def select_stage1(
        self,
        context: SwindleContext,
        info_result: InfoParseResult,
        normal_bestmove: str,
        reply_results: Optional[list[ReplySearchResult]] = None,
        *,
        run_probe: Optional[ProbeRunner] = None,
        set_backend_option: Optional[OptionSetter] = None,
        original_multipv: Optional[int] = None,
        mate_adapter: Optional[MateAdapter] = None,
    ) -> Stage1Decision:
        requested_mode = self.config.swindle_mode.strip().upper() or "HYBRID"

        events, option_restore_failed = self._collect_reply_events(reply_results)
        deferred_commands: list[str] = []
        quit_requested = False

        candidates = self._build_candidates(info_result)
        if not candidates:
            return Stage1Decision(
                normal_bestmove=normal_bestmove,
                selected_move=normal_bestmove,
                selected_reason="no_stage1_candidates",
                candidates=[],
                mate_detected=False,
                activated=False,
                mode=resolve_mode(requested_mode, context),
                mode_requested=requested_mode,
                events=events,
                option_restore_failed=option_restore_failed,
                deferred_commands=deferred_commands,
                quit_requested=quit_requested,
                mate_verify_status="not_used",
                verify_status_summary="not_used",
                verify_mode_used="VERIFY_ONLY",
                verify_engine_kind="backend",
                mate_verify_candidates_count=0,
                dfpn_used=False,
                dfpn_status_summary="not_used",
                dfpn_parser_hits=[],
                dfpn_parse_unknown_count=0,
                dfpn_distance_available_count=0,
                verify_conflict_count=0,
                verify_unknown_count=0,
                hybrid_learned_adjustment_used=False,
                hybrid_adjustment_delta=0.0,
                hybrid_adjustment_source="none",
                pseudo_hisshi_status="not_used",
                ponder_status_summary="not_used",
            )

        # Stage1 broad scan features + gate.
        best_base_cp = self._best_base_cp(candidates)
        drop_cap = context.dynamic_drop_cap_cp
        for cand in candidates:
            cand.features.mate_urgency = compute_mate_urgency(cand.mate_score)
            cand.features.threat_score = compute_threat_score(cand)
            apply_phase1_gate(cand, best_base_cp, drop_cap, self.config.swindle_mate_priority)

        # Stage2 focused reply search.
        stage2_results: list[ReplySearchResult] = []
        if run_probe is not None and set_backend_option is not None and context.root_position_cmd:
            stage2_results = self._run_stage2_reply_search(
                context=context,
                candidates=candidates,
                run_probe=run_probe,
                set_backend_option=set_backend_option,
                original_multipv=original_multipv,
            )
            for rs in stage2_results:
                events.extend(rs.events)
                option_restore_failed = option_restore_failed or rs.option_restore_failed
                deferred_commands.extend(rs.deferred_commands)
                if rs.quit_requested:
                    quit_requested = True

        # Stage2-derived features.
        pseudo_statuses: list[str] = []
        pseudo_budget_ms = self._pseudo_round_budget_ms(context)
        if hasattr(self.pseudo_hisshi, "begin_round"):
            try:
                self.pseudo_hisshi.begin_round(pseudo_budget_ms)
            except Exception:
                pass

        pseudo_budget_event_emitted = False
        pseudo_timeout_event_emitted = False
        for cand in candidates:
            replies = cand.reply_topk

            onlymove, gap12, gap13 = compute_onlymove_pressure(replies)
            cand.features.only_move_pressure = onlymove
            cand.features.gap12 = gap12
            cand.features.gap13 = gap13

            entropy_score, entropy = compute_reply_entropy(replies)
            cand.features.reply_entropy_score = entropy_score
            cand.features.reply_entropy = entropy

            cand.features.human_trap_score = compute_human_trap_score(replies, gap12=gap12, gap13=gap13)

            pseudo_result = self.pseudo_hisshi.estimate_with_status(
                cand,
                root_position_cmd=context.root_position_cmd,
                reply_topk=replies,
                emergency_fast_mode=context.emergency_fast_mode or (not getattr(self.config, "swindle_pseudo_hisshi_detect", True)),
                window_ply=int(getattr(self.config, "swindle_pseudo_hisshi_window_ply", 6)),
                run_probe=run_probe,
                max_probes=max(1, min(3, int(getattr(self.config, "swindle_reply_topk", 4)))),
            )
            cand.features.pseudo_hisshi_score = pseudo_result.score
            pseudo_statuses.append(pseudo_result.status)

            if pseudo_result.status == "skipped" and "emergency_fast_mode" in pseudo_result.notes:
                events.append("PSEUDO_HISSHI skipped")
            elif pseudo_result.status == "skipped_budget":
                if not pseudo_budget_event_emitted:
                    events.append("PSEUDO_HISSHI skipped_budget")
                    pseudo_budget_event_emitted = True
            elif pseudo_result.status == "timeout":
                if not pseudo_timeout_event_emitted:
                    events.append("PSEUDO_HISSHI timeout")
                    pseudo_timeout_event_emitted = True

            cand.features.self_risk = compute_self_risk(cand, replies)
            cand.features.survival_score = compute_survival_score(
                cand,
                replies,
                pseudo_hisshi_score=cand.features.pseudo_hisshi_score,
            )

            if replies:
                top = replies[0]
                if top.mate_raw is not None and top.mate_raw > 0:
                    cand.features.threat_score *= 0.45
                elif top.root_cp is not None and top.root_cp <= -1200:
                    cand.features.threat_score *= 0.7

        resolved_mode = resolve_mode(
            requested_mode,
            context,
            candidates,
            entropy_hint=self._entropy_hint(candidates),
        )
        scale = mode_weight_scale(resolved_mode)
        weights = RevWeights.from_config(self.config, scale)
        weights = self._tune_weights(weights, resolved_mode, context, candidates)

        verify_summary = self._apply_mate_verification(
            candidates=candidates,
            context=context,
            mate_adapter=mate_adapter,
            events=events,
        )
        pseudo_hisshi_status = self._summarize_pseudo_status(pseudo_statuses)

        for cand in candidates:
            apply_phase1_gate(cand, best_base_cp, drop_cap, self.config.swindle_mate_priority)
            rev_score, breakdown = compute_rev_score(cand, weights)
            cand.rev_score = rev_score
            cand.rev_breakdown = breakdown

        ranked = self._rank_candidates(candidates)

        mate_candidates = [
            c for c in ranked if (not c.gate_rejected) and c.mate_score is not None and c.mate_score > 0
        ]
        if mate_candidates:
            selected = min(mate_candidates, key=lambda c: abs(c.mate_score or 9999))
            return Stage1Decision(
                normal_bestmove=normal_bestmove,
                selected_move=selected.move,
                selected_reason="mate_priority",
                candidates=ranked,
                mate_detected=True,
                activated=True,
                mode=resolved_mode,
                mode_requested=requested_mode,
                events=events,
                option_restore_failed=option_restore_failed,
                deferred_commands=deferred_commands,
                quit_requested=quit_requested,
                mate_verify_status=verify_summary.status_summary,
                verify_status_summary=verify_summary.status_summary,
                verify_mode_used=verify_summary.verify_mode_used,
                verify_engine_kind=verify_summary.verify_engine_kind,
                mate_verify_candidates_count=verify_summary.candidates_count,
                dfpn_used=verify_summary.dfpn_used,
                dfpn_status_summary=verify_summary.dfpn_status_summary,
                dfpn_parser_hits=list(verify_summary.dfpn_parser_hits),
                dfpn_parse_unknown_count=verify_summary.dfpn_parse_unknown_count,
                dfpn_distance_available_count=verify_summary.dfpn_distance_available_count,
                dfpn_dialect_used=verify_summary.dfpn_dialect_used,
                dfpn_dialect_candidates=list(verify_summary.dfpn_dialect_candidates),
                dfpn_source_detail_normalized=verify_summary.dfpn_source_detail_normalized,
                dfpn_pack_source=verify_summary.dfpn_pack_source,
                dfpn_pack_version=verify_summary.dfpn_pack_version,
                dfpn_pack_load_errors=verify_summary.dfpn_pack_load_errors,
                verify_conflict_count=verify_summary.conflict_count,
                verify_unknown_count=verify_summary.unknown_count,
                hybrid_learned_adjustment_used=verify_summary.hybrid_adjustment_used,
                hybrid_adjustment_delta=verify_summary.hybrid_adjustment_delta,
                hybrid_adjustment_source=verify_summary.hybrid_adjustment_source,
                pseudo_hisshi_status=pseudo_hisshi_status,
                ponder_status_summary="not_used",
            )

        valid = [c for c in ranked if not c.gate_rejected]
        if valid:
            selected = valid[0]
            return Stage1Decision(
                normal_bestmove=normal_bestmove,
                selected_move=selected.move,
                selected_reason="rev_max",
                candidates=ranked,
                mate_detected=False,
                activated=True,
                mode=resolved_mode,
                mode_requested=requested_mode,
                events=events,
                option_restore_failed=option_restore_failed,
                deferred_commands=deferred_commands,
                quit_requested=quit_requested,
                mate_verify_status=verify_summary.status_summary,
                verify_status_summary=verify_summary.status_summary,
                verify_mode_used=verify_summary.verify_mode_used,
                verify_engine_kind=verify_summary.verify_engine_kind,
                mate_verify_candidates_count=verify_summary.candidates_count,
                dfpn_used=verify_summary.dfpn_used,
                dfpn_status_summary=verify_summary.dfpn_status_summary,
                dfpn_parser_hits=list(verify_summary.dfpn_parser_hits),
                dfpn_parse_unknown_count=verify_summary.dfpn_parse_unknown_count,
                dfpn_distance_available_count=verify_summary.dfpn_distance_available_count,
                dfpn_dialect_used=verify_summary.dfpn_dialect_used,
                dfpn_dialect_candidates=list(verify_summary.dfpn_dialect_candidates),
                dfpn_source_detail_normalized=verify_summary.dfpn_source_detail_normalized,
                dfpn_pack_source=verify_summary.dfpn_pack_source,
                dfpn_pack_version=verify_summary.dfpn_pack_version,
                dfpn_pack_load_errors=verify_summary.dfpn_pack_load_errors,
                verify_conflict_count=verify_summary.conflict_count,
                verify_unknown_count=verify_summary.unknown_count,
                hybrid_learned_adjustment_used=verify_summary.hybrid_adjustment_used,
                hybrid_adjustment_delta=verify_summary.hybrid_adjustment_delta,
                hybrid_adjustment_source=verify_summary.hybrid_adjustment_source,
                pseudo_hisshi_status=pseudo_hisshi_status,
                ponder_status_summary="not_used",
            )

        return Stage1Decision(
            normal_bestmove=normal_bestmove,
            selected_move=normal_bestmove,
            selected_reason="fallback_backend",
            candidates=ranked,
            mate_detected=False,
            activated=True,
            mode=resolved_mode,
            mode_requested=requested_mode,
            events=events,
            option_restore_failed=option_restore_failed,
            deferred_commands=deferred_commands,
            quit_requested=quit_requested,
            mate_verify_status=verify_summary.status_summary,
            verify_status_summary=verify_summary.status_summary,
            verify_mode_used=verify_summary.verify_mode_used,
            verify_engine_kind=verify_summary.verify_engine_kind,
            mate_verify_candidates_count=verify_summary.candidates_count,
            dfpn_used=verify_summary.dfpn_used,
            dfpn_status_summary=verify_summary.dfpn_status_summary,
            dfpn_parser_hits=list(verify_summary.dfpn_parser_hits),
            dfpn_parse_unknown_count=verify_summary.dfpn_parse_unknown_count,
            dfpn_distance_available_count=verify_summary.dfpn_distance_available_count,
            dfpn_dialect_used=verify_summary.dfpn_dialect_used,
            dfpn_dialect_candidates=list(verify_summary.dfpn_dialect_candidates),
            dfpn_source_detail_normalized=verify_summary.dfpn_source_detail_normalized,
            dfpn_pack_source=verify_summary.dfpn_pack_source,
            dfpn_pack_version=verify_summary.dfpn_pack_version,
            dfpn_pack_load_errors=verify_summary.dfpn_pack_load_errors,
            verify_conflict_count=verify_summary.conflict_count,
            verify_unknown_count=verify_summary.unknown_count,
            hybrid_learned_adjustment_used=verify_summary.hybrid_adjustment_used,
            hybrid_adjustment_delta=verify_summary.hybrid_adjustment_delta,
            hybrid_adjustment_source=verify_summary.hybrid_adjustment_source,
            pseudo_hisshi_status=pseudo_hisshi_status,
            ponder_status_summary="not_used",
        )

    def _run_stage2_reply_search(
        self,
        *,
        context: SwindleContext,
        candidates: list[CandidateMove],
        run_probe: ProbeRunner,
        set_backend_option: OptionSetter,
        original_multipv: Optional[int],
    ) -> list[ReplySearchResult]:
        results: list[ReplySearchResult] = []
        reply_search = ReplySearch(set_backend_option=set_backend_option, run_probe=run_probe)

        adaptive = bool(getattr(self.config, "swindle_use_adaptive_reply_budget", True))
        stage2_max = int(getattr(self.config, "swindle_max_candidates", 6))
        if context.emergency_fast_mode:
            stage2_max = min(stage2_max, 2)

        reply_topk = int(getattr(self.config, "swindle_reply_topk", 4))
        if context.emergency_fast_mode:
            reply_topk = min(reply_topk, 2)

        probe_multipv = int(getattr(self.config, "swindle_reply_multipv", 4))
        if adaptive and context.time_left_ms is not None:
            reserve_ms = int(getattr(self.config, "swindle_reserve_time_ms", 200))
            budget_ms = max(0, context.time_left_ms - reserve_ms)
            if budget_ms <= 3000:
                stage2_max = min(stage2_max, 2)
                reply_topk = min(reply_topk, 2)
                probe_multipv = min(probe_multipv, 3)
            elif budget_ms <= 8000:
                stage2_max = min(stage2_max, 4)
                reply_topk = min(reply_topk, 3)
                probe_multipv = min(probe_multipv, 4)
        probe_multipv = max(2, probe_multipv)
        root_multipv = original_multipv if original_multipv is not None else int(getattr(self.config, "swindle_multipv", 12))

        targets = [c for c in candidates if not c.gate_rejected]
        if not targets:
            targets = list(candidates)

        targets.sort(key=self._stage2_target_key, reverse=True)
        targets = targets[: max(1, stage2_max)]

        go_cmd = self._build_reply_go_cmd(context)
        for cand in targets:
            position_cmd = _append_move(context.root_position_cmd, cand.move)
            result = reply_search.analyze(
                position_cmd=position_cmd,
                go_cmd=go_cmd,
                original_multipv=root_multipv,
                probe_multipv=probe_multipv,
                reply_topk=reply_topk,
            )
            cand.reply_topk = list(result.reply_topk)
            results.append(result)
            if result.quit_requested:
                break
        return results

    def _build_reply_go_cmd(self, context: SwindleContext) -> str:
        adaptive = bool(getattr(self.config, "swindle_use_adaptive_reply_budget", True))
        reserve_ms = int(getattr(self.config, "swindle_reserve_time_ms", 200))
        reply_nodes = int(getattr(self.config, "swindle_reply_nodes", 0))
        if reply_nodes > 0:
            if adaptive and context.time_left_ms is not None:
                budget_ms = max(0, context.time_left_ms - reserve_ms)
                if budget_ms <= 2500:
                    reply_nodes = max(1, min(reply_nodes, 60_000))
                elif budget_ms <= 7000:
                    reply_nodes = max(1, min(reply_nodes, 120_000))
            return f"go nodes {reply_nodes}"

        reply_depth = int(getattr(self.config, "swindle_reply_depth", 10))
        if context.emergency_fast_mode:
            reply_depth = max(4, min(reply_depth, 8))
        elif adaptive and context.time_left_ms is not None:
            budget_ms = max(0, context.time_left_ms - reserve_ms)
            if budget_ms <= 3000:
                reply_depth = max(4, min(reply_depth, 7))
            elif budget_ms <= 8000:
                reply_depth = max(4, min(reply_depth, 9))
        return f"go depth {reply_depth}"

    def _apply_mate_verification(
        self,
        *,
        candidates: list[CandidateMove],
        context: SwindleContext,
        mate_adapter: Optional[MateAdapter],
        events: list[str],
    ) -> VerifySummary:
        use_verify = bool(getattr(self.config, "swindle_use_mate_engine_verification", False))
        mode_requested = str(getattr(self.config, "swindle_verify_mode", "VERIFY_ONLY")).strip().upper() or "VERIFY_ONLY"
        mode_used = mode_requested
        if context.emergency_fast_mode and mode_requested != "VERIFY_ONLY":
            mode_used = "VERIFY_ONLY"
            events.append(f"VERIFY mode downgraded:{mode_requested}->VERIFY_ONLY")

        if not use_verify:
            for cand in candidates:
                cand.mate_verify_status = "not_used"
            return VerifySummary(
                status_summary="not_used",
                verify_mode_used=mode_used,
                dfpn_parser_hits=[],
                dfpn_parse_unknown_count=0,
                dfpn_distance_available_count=0,
                conflict_count=0,
                unknown_count=0,
                hybrid_adjustment_used=False,
                hybrid_adjustment_delta=0.0,
                hybrid_adjustment_source="none",
            )

        if mate_adapter is None or not mate_adapter.available():
            for cand in candidates:
                cand.mate_verify_status = "skipped"
            return VerifySummary(
                status_summary="skipped",
                verify_mode_used=mode_used,
                dfpn_parser_hits=[],
                dfpn_parse_unknown_count=0,
                dfpn_distance_available_count=0,
                conflict_count=0,
                unknown_count=0,
                hybrid_adjustment_used=False,
                hybrid_adjustment_delta=0.0,
                hybrid_adjustment_source="none",
            )

        verify_ms = int(getattr(self.config, "swindle_mate_verify_time_ms", 300))
        aggressive_extra = int(getattr(self.config, "swindle_verify_aggressive_extra_ms", 0))
        targets = self._verify_targets(candidates, mode_used)
        statuses: list[str] = []
        engine_kinds: list[str] = []
        dfpn_statuses: list[str] = []
        dfpn_parser_states: list[str] = []
        dfpn_parser_hits: list[str] = []
        dfpn_dialect_used_values: list[str] = []
        dfpn_dialect_candidates_agg: list[str] = []
        dfpn_source_detail_norm_values: list[str] = []
        dfpn_pack_source_values: list[str] = []
        dfpn_pack_version_values: list[str] = []
        dfpn_pack_load_errors_max = 0
        dfpn_used = False
        dfpn_parse_unknown_count = 0
        dfpn_distance_available_count = 0
        conflict_count = 0
        unknown_count = 0
        adj_used = False
        adj_delta_sum = 0.0
        adj_delta_n = 0
        adj_source = "none"

        for cand in targets:
            try:
                verify_budget_ms = verify_ms
                if mode_used == "AGGRESSIVE":
                    verify_budget_ms += max(0, aggressive_extra)
                result = mate_adapter.verify(
                    context.root_sfen,
                    cand.move,
                    verify_budget_ms,
                    mode=mode_used,
                    root_position_cmd=context.root_position_cmd,
                )
                status = getattr(result, "status", None)
                if not status:
                    status = "confirmed" if result.found_mate else "not_used"
                cand.mate_verify_status = status
                statuses.append(status)
                if status == "unknown":
                    unknown_count += 1
                    dfpn_parse_unknown_count += 1
                engine_kind = getattr(result, "engine_kind", None) or "backend"
                engine_kinds.append(engine_kind)
                if engine_kind in {"dfpn", "hybrid"}:
                    dfpn_used = True
                if getattr(result, "distance", None) is not None:
                    dfpn_distance_available_count += 1
                d_used = getattr(result, "dfpn_dialect_used", None)
                if isinstance(d_used, str) and d_used:
                    dfpn_dialect_used_values.append(d_used)
                d_candidates = getattr(result, "dfpn_dialect_candidates", None)
                if isinstance(d_candidates, list):
                    for item in d_candidates:
                        if isinstance(item, str) and item:
                            dfpn_dialect_candidates_agg.append(item)
                d_norm = getattr(result, "dfpn_source_detail_normalized", None)
                if isinstance(d_norm, str) and d_norm:
                    dfpn_source_detail_norm_values.append(d_norm)
                pack_source = getattr(result, "dfpn_pack_source", None)
                if isinstance(pack_source, str) and pack_source:
                    dfpn_pack_source_values.append(pack_source)
                pack_version = getattr(result, "dfpn_pack_version", None)
                if isinstance(pack_version, str) and pack_version:
                    dfpn_pack_version_values.append(pack_version)
                try:
                    pack_errs = int(getattr(result, "dfpn_pack_load_errors", 0) or 0)
                    if pack_errs > dfpn_pack_load_errors_max:
                        dfpn_pack_load_errors_max = pack_errs
                except Exception:
                    pass
                if getattr(result, "hybrid_learned_adjustment_used", False):
                    adj_used = True
                    try:
                        adj_delta_sum += float(getattr(result, "hybrid_adjustment_delta", 0.0))
                        adj_delta_n += 1
                    except Exception:
                        pass
                    src = str(getattr(result, "hybrid_adjustment_source", "none") or "none")
                    if src != "none":
                        adj_source = src

                if result.found_mate:
                    cand.features.mate_chance = max(cand.features.mate_chance, max(0.2, float(result.confidence)))
                    if cand.mate_score is None:
                        cand.features.mate_urgency = max(cand.features.mate_urgency, 0.20)
                if status == "rejected":
                    cand.features.self_risk = max(cand.features.self_risk, 0.95)
                    cand.gate_rejected = True
                    if not cand.gate_reason:
                        cand.gate_reason = "verify_rejected"

                if status == "timeout":
                    events.append("VERIFY timeout")
                elif status == "error":
                    events.append("VERIFY error")
                elif status == "rejected":
                    events.append("VERIFY rejected")

                for note in getattr(result, "notes", [])[:4]:
                    if not isinstance(note, str):
                        continue
                    if note.startswith("hybrid_conflict"):
                        conflict_count += 1
                    if note.startswith("dfpn_status:"):
                        dfpn_status = note.split(":", 1)[1] or "not_used"
                        dfpn_statuses.append(dfpn_status)
                        if dfpn_status in {"timeout", "error", "skipped"}:
                            events.append(f"dfpn_{dfpn_status}")
                        if dfpn_status == "unknown":
                            dfpn_parse_unknown_count += 1
                    elif note.startswith("dfpn_parser:"):
                        parser_state = note.split(":", 1)[1] or "unknown"
                        dfpn_parser_states.append(parser_state)
                    elif note.startswith("dfpn_hit:"):
                        hit = note.split(":", 1)[1] or ""
                        if hit:
                            dfpn_parser_hits.append(hit)
                    elif note.startswith("dfpn_"):
                        events.append(note)
                src = getattr(result, "source_detail", None)
                if isinstance(src, str) and src.startswith("dfpn:"):
                    token = src[len("dfpn:") :]
                    if token:
                        dfpn_parser_hits.append(token)
            except TimeoutError:
                cand.mate_verify_status = "timeout"
                statuses.append("timeout")
                events.append("VERIFY timeout")
            except Exception:
                cand.mate_verify_status = "skipped"
                statuses.append("skipped")
                events.append("VERIFY_ONLY skipped")

        for cand in candidates:
            if cand.mate_verify_status is None:
                cand.mate_verify_status = "not_used"

        status_summary = "not_used"
        if "rejected" in statuses:
            status_summary = "rejected"
        elif "confirmed" in statuses:
            status_summary = "confirmed"
        elif "unknown" in statuses:
            status_summary = "unknown"
        elif "timeout" in statuses:
            status_summary = "timeout"
        elif "error" in statuses:
            status_summary = "error"
        elif "skipped" in statuses:
            status_summary = "skipped"

        engine_kind_summary = "backend"
        if "hybrid" in engine_kinds:
            engine_kind_summary = "hybrid"
        elif "mate_engine" in engine_kinds:
            engine_kind_summary = "mate_engine"
        elif "dfpn" in engine_kinds:
            engine_kind_summary = "dfpn"

        dfpn_summary = "not_used"
        if dfpn_statuses:
            if "error" in dfpn_statuses:
                dfpn_summary = "error"
            elif "timeout" in dfpn_statuses:
                dfpn_summary = "timeout"
            elif "confirmed" in dfpn_statuses:
                dfpn_summary = "confirmed"
            elif "rejected" in dfpn_statuses:
                dfpn_summary = "rejected"
            elif "unknown" in dfpn_statuses:
                dfpn_summary = "unknown"
            elif "skipped" in dfpn_statuses:
                dfpn_summary = "skipped"
            else:
                dfpn_summary = dfpn_statuses[0]
        elif dfpn_parser_states:
            if "error" in dfpn_parser_states:
                dfpn_summary = "error"
            elif "unknown" in dfpn_parser_states:
                dfpn_summary = "unknown"
            elif "partial" in dfpn_parser_states:
                dfpn_summary = "partial"
            else:
                dfpn_summary = dfpn_parser_states[0]

        return VerifySummary(
            status_summary=status_summary,
            verify_mode_used=mode_used,
            verify_engine_kind=engine_kind_summary,
            candidates_count=len(targets),
            dfpn_used=dfpn_used,
            dfpn_status_summary=dfpn_summary,
            dfpn_parser_hits=_dedup_keep_order(dfpn_parser_hits),
            dfpn_parse_unknown_count=dfpn_parse_unknown_count,
            dfpn_distance_available_count=dfpn_distance_available_count,
            dfpn_dialect_used=(dfpn_dialect_used_values[0] if dfpn_dialect_used_values else "none"),
            dfpn_dialect_candidates=_dedup_keep_order(dfpn_dialect_candidates_agg),
            dfpn_source_detail_normalized=(
                dfpn_source_detail_norm_values[0] if dfpn_source_detail_norm_values else "none"
            ),
            dfpn_pack_source=_summarize_pack_source(dfpn_pack_source_values),
            dfpn_pack_version=(dfpn_pack_version_values[0] if dfpn_pack_version_values else "unknown"),
            dfpn_pack_load_errors=dfpn_pack_load_errors_max,
            conflict_count=conflict_count,
            unknown_count=unknown_count,
            hybrid_adjustment_used=adj_used,
            hybrid_adjustment_delta=(adj_delta_sum / float(adj_delta_n)) if adj_delta_n > 0 else 0.0,
            hybrid_adjustment_source=adj_source,
        )

    def _verify_targets(self, candidates: list[CandidateMove], mode_used: str) -> list[CandidateMove]:
        ordered = sorted(candidates, key=self._rank_key, reverse=True)
        if not ordered:
            return []

        def uniq(xs: list[CandidateMove]) -> list[CandidateMove]:
            seen: set[str] = set()
            out: list[CandidateMove] = []
            for c in xs:
                if c.move in seen:
                    continue
                seen.add(c.move)
                out.append(c)
            return out

        mate_suspects = [c for c in ordered if c.mate_score is not None or c.features.mate_urgency > 0.0]
        top = ordered[0]
        base = [top] + mate_suspects[:2]

        if mode_used == "VERIFY_ONLY":
            return uniq(base)[:3]

        max_n = max(1, int(getattr(self.config, "swindle_verify_max_candidates", 4)))
        if mode_used == "TOP_CANDIDATES":
            merged = uniq(mate_suspects + ordered)
            return merged[:max_n]

        # AGGRESSIVE
        merged = uniq(mate_suspects + ordered)
        extra = 2
        return merged[: min(len(merged), max_n + extra)]

    def _stage2_target_key(self, candidate: CandidateMove) -> tuple[int, int, int, int]:
        mate_bucket = 1 if (candidate.mate_score is not None and candidate.mate_score > 0) else 0
        mate_short = -(abs(candidate.mate_score) if candidate.mate_score is not None else 9999)
        cp = candidate.base_cp if candidate.base_cp is not None else -10**9
        return (mate_bucket, candidate.depth, mate_short, cp)

    def _tune_weights(
        self,
        weights: RevWeights,
        mode: str,
        context: SwindleContext,
        candidates: list[CandidateMove],
    ) -> RevWeights:
        tune = getattr(self.weight_tuner, "tune", None)
        if callable(tune):
            try:
                tuned = tune(weights, mode, context, candidates)
                if isinstance(tuned, RevWeights):
                    return tuned
            except Exception:
                return weights
        return weights

    def _entropy_hint(self, candidates: list[CandidateMove]) -> float:
        xs = [c.features.reply_entropy for c in candidates if c.features.reply_entropy > 0.0]
        if not xs:
            return 0.0
        return sum(xs) / float(len(xs))

    def _pseudo_round_budget_ms(self, context: SwindleContext) -> int:
        if context.emergency_fast_mode:
            return 0
        if context.time_left_ms is None:
            return 450
        reserve = int(getattr(self.config, "swindle_reserve_time_ms", 200))
        remain = max(0, context.time_left_ms - reserve)
        return max(120, min(1200, int(remain * 0.10)))

    def _summarize_pseudo_status(self, statuses: list[str]) -> str:
        if not statuses:
            return "not_used"
        if "ok" in statuses:
            return "ok"
        if "timeout" in statuses:
            return "timeout"
        if "skipped_budget" in statuses:
            return "skipped_budget"
        if "skipped" in statuses:
            return "skipped"
        return statuses[0]

    def _collect_reply_events(
        self,
        reply_results: Optional[list[ReplySearchResult]],
    ) -> tuple[list[str], bool]:
        events: list[str] = []
        restore_failed = False

        if not reply_results:
            return events, restore_failed

        for result in reply_results:
            events.extend(result.events)
            if result.option_restore_failed:
                restore_failed = True
        return events, restore_failed

    def _build_candidates(self, info_result: InfoParseResult) -> list[CandidateMove]:
        snapshots = list(info_result.by_move.values())
        snapshots.sort(key=lambda s: ((s.depth or 0), -(s.multipv or 1)), reverse=True)
        candidates: list[CandidateMove] = []
        for snap in snapshots:
            if not snap.move:
                continue
            candidates.append(self._snapshot_to_candidate(snap))

        candidates.sort(key=lambda c: c.depth, reverse=True)
        return candidates[: self.config.swindle_max_candidates]

    def _snapshot_to_candidate(self, snap: InfoSnapshot) -> CandidateMove:
        return CandidateMove(
            move=snap.move or "",
            pv=list(snap.pv),
            base_cp=snap.cp,
            mate_score=snap.mate,
            depth=snap.depth or 0,
            seldepth=snap.seldepth,
            nodes=snap.nodes,
            nps=snap.nps,
            hashfull=snap.hashfull,
            multipv=snap.multipv,
        )

    def _best_base_cp(self, candidates: list[CandidateMove]) -> Optional[int]:
        non_mate_cp = [
            c.base_cp for c in candidates if c.base_cp is not None and (c.mate_score is None or c.mate_score <= 0)
        ]
        if non_mate_cp:
            return max(non_mate_cp)
        all_cp = [c.base_cp for c in candidates if c.base_cp is not None]
        if all_cp:
            return max(all_cp)
        return None

    def _rank_key(self, candidate: CandidateMove) -> tuple[int, int, int, float, int]:
        non_rejected = 1 if not candidate.gate_rejected else 0
        if candidate.mate_score is not None and candidate.mate_score > 0:
            mate_bucket = 2
            mate_short = -(abs(candidate.mate_score))
        elif candidate.mate_score is not None and candidate.mate_score < 0:
            mate_bucket = 0
            mate_short = -99999
        else:
            mate_bucket = 1
            mate_short = 0
        cp = candidate.base_cp if candidate.base_cp is not None else -10**9
        return (non_rejected, mate_bucket, mate_short, candidate.rev_score, cp)

    def _rank_candidates(self, candidates: list[CandidateMove]) -> list[CandidateMove]:
        return sorted(candidates, key=self._rank_key, reverse=True)


def _append_move(position_cmd: str, move: str) -> str:
    if not move:
        return position_cmd
    if " moves " in position_cmd:
        return f"{position_cmd} {move}"
    return f"{position_cmd} moves {move}"


def _dedup_keep_order(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _summarize_pack_source(values: list[str]) -> str:
    if not values:
        return "builtin"
    if "external_fallback_builtin" in values:
        return "external_fallback_builtin"
    if "external" in values:
        return "external"
    if "builtin" in values:
        return "builtin"
    return values[0]
