from __future__ import annotations

import os
import queue
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import SwindleConfig
from .engine_process import EngineCommand, EngineProcess
from .engine_session import EngineSession, QueueReadable
from .info_parser import InfoParseResult, InfoSnapshot
from .logging import DecisionCandidateRecord, DecisionEvent, JsonlLogger
from .mate import MateAdapter
from .position_state import PositionState
from .swindle import Stage1Decision, SwindleContext, SwindleController
from .swindle.pseudo_hisshi import PseudoHisshiEstimator
from .swindle.reply_search import ProbeOutcome
from .swindle.weight_tuner import WeightTuner
from .usi_messages import (
    is_special_bestmove,
    is_usi_move_token,
    parse_option_name,
    parse_setoption,
)


class StdinReader(QueueReadable):
    """
    Non-blocking stdin reader.

    Reference: nnue_proxy.py:219 StdinReader
    """

    def __init__(self) -> None:
        self.q: queue.Queue[str] = queue.Queue()
        self.alive = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                self.q.put(line)
            self.alive = False
        except Exception:
            self.alive = False

    def get(self, timeout: Optional[float] = None) -> Optional[str]:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_nowait(self) -> Optional[str]:
        try:
            return self.q.get_nowait()
        except queue.Empty:
            return None

    def put_back(self, line: str) -> None:
        self.q.put(line)


@dataclass
class PonderCacheEntry:
    position_cmd: str
    info_result: InfoParseResult
    created_ts: float
    root_eval_cp: Optional[int] = None
    candidate_count: int = 0
    top_gap12: Optional[float] = None
    had_mate_signal: bool = False
    elapsed_ms: int = 0
    reply_coverage: float = 0.0
    verify_done: bool = False
    predicted_move: Optional[str] = None
    decision_id: Optional[str] = None
    parent_position_key: Optional[str] = None


class USIProtocol:
    """
    Top-level USI wrapper protocol state machine.

    Reference: nnue_proxy.py:1686 main loop design
    """

    def __init__(self, config: SwindleConfig) -> None:
        self.config = config
        self.stdin_reader = StdinReader()
        self.position_state = PositionState()

        self.mate_adapter = MateAdapter(config.mate_engine_path)
        self.controller = SwindleController(
            config=config,
            pseudo_hisshi=PseudoHisshiEstimator(),
            weight_tuner=WeightTuner(),
        )
        self.logger = JsonlLogger(config)

        self.engine: Optional[EngineProcess] = None
        self.engine_session: Optional[EngineSession] = None

        self.backend_initialized = False
        self.backend_supported_options: set[str] = set()
        self.backend_option_lines: list[str] = []
        self.backend_id_name = ""
        self.backend_id_author = ""

        self.restart_backend_required = False
        self.quit_requested = False
        self.backend_restart_count_total = 0
        self._ponder_cache: Optional[PonderCacheEntry] = None
        self._pending_feedback: Optional[dict[str, object]] = None
        self._feedback_actual_opponent_move: Optional[str] = None
        self._feedback_actual_move_in_reply_topk: Optional[bool] = None
        self._feedback_actual_move_rank_in_reply_topk: Optional[int] = None
        self._feedback_outcome_tag: Optional[str] = None
        self._feedback_outcome_confidence: Optional[float] = None
        self._ponder_gate_weights_loaded_path: str = ""
        self._last_ponder_gate_adjustment_used: bool = False
        self._last_ponder_gate_adjustment_delta: float = 0.0
        self._last_ponder_gate_adjustment_source: str = "none"
        self._last_ponder_reuse_meta: Optional[dict[str, Optional[str]]] = None
        self._sync_mate_adapter_config()
        self._sync_ponder_gate_weights_config()

    def run(self) -> None:
        try:
            while not self.quit_requested:
                line = self.stdin_reader.get(timeout=0.1)
                if line is None:
                    if not self.stdin_reader.alive and self.stdin_reader.q.empty():
                        break
                    continue

                if line == "usi":
                    self._handle_usi()
                    continue

                if line == "isready":
                    self._handle_isready()
                    continue

                if line.startswith("setoption"):
                    self._handle_setoption(line)
                    continue

                if line == "usinewgame":
                    self._handle_usinewgame()
                    continue

                if line.startswith("position"):
                    self._handle_position(line)
                    continue

                if line.startswith("go"):
                    self._handle_go(line)
                    continue

                if line == "stop":
                    if self.engine is not None:
                        self.engine.send("stop")
                    continue

                if line == "quit":
                    self.quit_requested = True
                    break

                self._forward_to_backend(line)
        finally:
            self._shutdown()

    def _out(self, text: str) -> None:
        try:
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
        except BrokenPipeError:
            self.quit_requested = True

    def _info(self, text: str, level: int = 1) -> None:
        if self.config.swindle_emit_info_string_level < level:
            return
        self._out(f"info string {text}")

    def _dbg(self, text: str) -> None:
        if not self.config.swindle_verbose_info:
            return
        try:
            sys.stderr.write(f"[TASO-SWINDLE] {text}\n")
            sys.stderr.flush()
        except Exception:
            pass

    def _handle_usi(self) -> None:
        backend_ok = self._ensure_engine_started()
        if backend_ok:
            self._initialize_backend_usi()

        self._out(f"id name {self.config.engine_name}")
        self._out(f"id author {self.config.engine_author}")

        wrapper_option_names = {spec.name for spec in self.config._specs_by_name.values()}
        for line in self.backend_option_lines:
            name = parse_option_name(line)
            if name and name in wrapper_option_names:
                continue
            self._out(line)

        for line in self.config.iter_usi_option_lines():
            self._out(line)

        self._out("usiok")

    def _handle_isready(self) -> None:
        ready = self._ensure_engine_started()
        if ready:
            self._initialize_backend_usi()
            self._apply_backend_options()
            self._wait_backend_ready()
        self._out("readyok")

    def _handle_setoption(self, line: str) -> None:
        cmd = parse_setoption(line)
        if cmd is None:
            return

        applied = self.config.apply_usi_option(cmd.name, cmd.value)
        if applied.handled:
            if cmd.name in {"MateEnginePath", "SwindleMateEnginePath"}:
                self.mate_adapter = MateAdapter(self.config.mate_engine_path)
            self._sync_mate_adapter_config()
            self._sync_ponder_gate_weights_config()
            if applied.restart_required:
                self.restart_backend_required = True
            return

        if not self._ensure_engine_started():
            return
        self._initialize_backend_usi()
        assert self.engine is not None
        self.engine.send(line)

    def _handle_usinewgame(self) -> None:
        self.position_state.on_new_game()
        self._pending_feedback = None
        self._clear_feedback_annotations()
        self._forward_to_backend("usinewgame")

    def _handle_position(self, line: str) -> None:
        self._update_feedback_from_position(line)
        self.position_state.update_from_command(line)
        self._ponder_cache = None
        self._forward_to_backend(line)

    def _handle_go(self, go_line: str) -> None:
        restart_before = self.backend_restart_count_total
        normal_bestmove = "resign"
        final_bestmove = "resign"
        selected_reason = "fallback_resign"
        time_info = self._parse_go_time_info(go_line)
        go_tokens = go_line.split()
        is_ponder = "ponder" in go_tokens
        outcome = None
        ponder_cache_hit = False
        ponder_cache_used = False
        ponder_used_budget_ms = 0
        ponder_fallback_reason: Optional[str] = None
        ponder_reuse_score: Optional[float] = None
        ponder_cache_age_ms = 0
        ponder_cache_gate_reason: Optional[str] = None
        ponder_gate_learned_adjustment_used = False
        ponder_gate_adjustment_delta = 0.0
        ponder_gate_adjustment_source = "none"
        reuse_then_bestmove_changed = False
        ponder_reuse_decision_id: Optional[str] = None
        ponder_reuse_parent_position_key: Optional[str] = None
        ponder_label_source = "heuristic"
        ponder_label_confidence = 0.0

        side = self.position_state.side_to_move()
        time_left_ms = time_info.get("btime") if side == "b" else time_info.get("wtime")
        byoyomi_ms = time_info.get("byoyomi")
        increment_ms = time_info.get("binc") if side == "b" else time_info.get("winc")
        emergency = bool(time_left_ms is not None and time_left_ms <= self.config.swindle_emergency_fast_mode_ms)
        if is_ponder:
            emergency = True

        context = SwindleContext(
            side_to_move=side,
            root_sfen=self.position_state.root_sfen,
            root_position_cmd=self.position_state.raw_position,
            root_eval_cp=None,
            root_mate_score=None,
            is_losing=True,
            is_lost=False,
            time_left_ms=time_left_ms,
            byoyomi_ms=byoyomi_ms,
            increment_ms=increment_ms,
            mode=self.config.swindle_mode,
            swindle_enabled=self.config.swindle_enable,
            emergency_fast_mode=emergency,
            dynamic_drop_cap_cp=self.config.dynamic_drop_cap_cp(None, None),
        )
        decision = Stage1Decision(
            normal_bestmove=normal_bestmove,
            selected_move=normal_bestmove,
            selected_reason="fallback_backend",
            candidates=[],
            mate_detected=False,
            activated=False,
            mode=context.mode,
            events=[],
            option_restore_failed=False,
            ponder_status_summary="not_used",
        )
        activate = False
        ponder_status_summary = "not_used"
        pre_events: list[str] = []
        cached_info_for_selection: Optional[InfoParseResult] = None
        self._reset_last_ponder_gate_adjustment()
        self._last_ponder_reuse_meta = None
        if not is_ponder and self.config.swindle_ponder_enable:
            (
                cached_info_for_selection,
                ponder_cache_hit,
                had_cache,
                ponder_reuse_score,
                ponder_cache_age_ms,
                ponder_cache_gate_reason,
            ) = self._consume_ponder_cache(
                self.position_state.raw_position,
                max_age_sec=max(0.0, float(self.config.swindle_ponder_cache_max_age_ms) / 1000.0),
            )
            ponder_gate_learned_adjustment_used = self._last_ponder_gate_adjustment_used
            ponder_gate_adjustment_delta = self._last_ponder_gate_adjustment_delta
            ponder_gate_adjustment_source = self._last_ponder_gate_adjustment_source
            if had_cache and cached_info_for_selection is None:
                if ponder_cache_gate_reason:
                    pre_events.append(f"ponder_cache_{ponder_cache_gate_reason}")
                elif not ponder_cache_hit:
                    pre_events.append("ponder_cache_miss")

        try:
            self._sync_mate_adapter_config()
            if not self._ensure_engine_started():
                self._out("bestmove resign")
                return
            if not self._initialize_backend_usi():
                self._out("bestmove resign")
                return

            self._apply_backend_options()

            assert self.engine is not None
            if self.engine_session is None:
                self.engine_session = EngineSession(self.engine, self.config)

            outcome = self.engine_session.run_go(
                go_line=go_line,
                stdin_reader=self.stdin_reader,
                # Keep GUI analysis/consideration panes alive by streaming backend info lines.
                forward_engine_info=True,
                on_engine_line=self._out,
            )

            if outcome.quit_requested:
                self.quit_requested = True
                return

            normal_bestmove = self._normalize_bestmove(outcome.backend_bestmove)
            root = outcome.info_result.by_multipv.get(1)
            root_cp = root.cp if root is not None else None
            root_mate = root.mate if root is not None else None

            context = SwindleContext(
                side_to_move=side,
                root_sfen=self.position_state.root_sfen,
                root_position_cmd=self.position_state.raw_position,
                root_eval_cp=root_cp,
                root_mate_score=root_mate,
                is_losing=(root_cp is not None and root_cp <= self.config.swindle_eval_threshold_cp) or (root_mate is not None and root_mate < 0),
                is_lost=(root_cp is not None and root_cp <= self.config.swindle_eval_threshold_cp - 600) or (root_mate is not None and root_mate < 0),
                time_left_ms=time_left_ms,
                byoyomi_ms=byoyomi_ms,
                increment_ms=increment_ms,
                mode=self.config.swindle_mode,
                swindle_enabled=self.config.swindle_enable,
                emergency_fast_mode=emergency,
                dynamic_drop_cap_cp=self.config.dynamic_drop_cap_cp(root_cp, root_mate),
            )

            if outcome.backend_dead:
                self.restart_backend_required = True
                if self._ensure_engine_started():
                    decision.events.append("BACKEND restart")
                final_bestmove = normal_bestmove
                selected_reason = "fallback_backend"
            else:
                pending_events: list[str] = list(pre_events)
                activate = self._should_activate_swindle(context)
                mate_adapter_for_select = self.mate_adapter
                info_for_selection = outcome.info_result
                if cached_info_for_selection is not None:
                    info_for_selection = self._merge_info_results(outcome.info_result, cached_info_for_selection)
                    ponder_cache_used = True
                    pending_events.append("ponder_cache_hit")
                if is_ponder:
                    if not self.config.swindle_ponder_enable:
                        activate = False
                        ponder_status_summary = "fallback"
                        pending_events.append("PONDER fallback")
                        ponder_fallback_reason = "ponder_disabled"
                    else:
                        ponder_status_summary = "ok"
                        if not self.config.swindle_ponder_verify:
                            mate_adapter_for_select = None
                if activate:
                    def run_probe(position_cmd: str, go_cmd: str) -> ProbeOutcome:
                        assert self.engine_session is not None
                        assert self.engine is not None
                        self.engine.send(position_cmd)
                        probe_outcome = self.engine_session.run_go(
                            go_line=go_cmd,
                            stdin_reader=self.stdin_reader,
                            forward_engine_info=False,
                            on_engine_line=None,
                        )
                        if probe_outcome.backend_dead:
                            self.restart_backend_required = True
                            self._ensure_engine_started()
                        return ProbeOutcome(
                            info_result=probe_outcome.info_result if probe_outcome.info_result is not None else InfoParseResult(),
                            timed_out=probe_outcome.timed_out,
                            quit_requested=probe_outcome.quit_requested,
                            deferred_commands=list(probe_outcome.deferred_commands),
                            bestmove=probe_outcome.backend_bestmove,
                            backend_dead=probe_outcome.backend_dead,
                        )

                    def set_backend_option(name: str, value: str) -> None:
                        if self.engine is None:
                            return
                        # Probe-time option mutation is limited to MultiPV.
                        if name != "MultiPV":
                            return
                        self.engine.send(f"setoption name {name} value {value}")

                    restore_ponder_dfpn: Optional[bool] = None
                    if (
                        is_ponder
                        and self.config.swindle_ponder_enable
                        and mate_adapter_for_select is not None
                        and not self.config.swindle_ponder_dfpn
                        and getattr(mate_adapter_for_select, "use_dfpn", False)
                    ):
                        restore_ponder_dfpn = bool(getattr(mate_adapter_for_select, "use_dfpn", False))
                        mate_adapter_for_select.use_dfpn = False
                        pending_events.append("PONDER dfpn_off")
                    decision_stage_start = time.time()
                    try:
                        decision = self.controller.select_stage1(
                            context,
                            info_for_selection,
                            normal_bestmove,
                            run_probe=run_probe,
                            set_backend_option=set_backend_option,
                            original_multipv=self.config.swindle_multipv,
                            mate_adapter=mate_adapter_for_select,
                        )
                    finally:
                        if restore_ponder_dfpn is not None and mate_adapter_for_select is not None:
                            mate_adapter_for_select.use_dfpn = restore_ponder_dfpn
                    ponder_used_budget_ms = int(max(0.0, (time.time() - decision_stage_start) * 1000.0))
                    if is_ponder:
                        max_ms = max(0, int(getattr(self.config, "swindle_ponder_max_ms", 500)))
                        if max_ms <= 0:
                            pending_events.append("ponder_timeout")
                            ponder_status_summary = "timeout"
                            ponder_fallback_reason = "max_ms_zero"
                            activate = False
                            decision = Stage1Decision(
                                normal_bestmove=normal_bestmove,
                                selected_move=normal_bestmove,
                                selected_reason="ponder_timeout_fallback",
                                candidates=[],
                                mate_detected=False,
                                activated=False,
                                mode=context.mode,
                                events=[],
                                option_restore_failed=decision.option_restore_failed,
                                ponder_status_summary=ponder_status_summary,
                            )
                        elif ponder_used_budget_ms > max_ms:
                            pending_events.append("ponder_budget_exceeded")
                            ponder_status_summary = "fallback"
                            ponder_fallback_reason = "budget_exceeded"
                else:
                    decision = Stage1Decision(
                        normal_bestmove=normal_bestmove,
                        selected_move=normal_bestmove,
                        selected_reason="swindle_not_activated",
                        candidates=[],
                        mate_detected=False,
                        activated=False,
                        mode=context.mode,
                        events=[],
                        option_restore_failed=False,
                        ponder_status_summary=ponder_status_summary,
                    )

                if pending_events:
                    decision.events = [*pending_events, *decision.events]

                if decision.quit_requested:
                    self.quit_requested = True
                    return

                if is_ponder and decision.ponder_status_summary == "not_used":
                    decision.ponder_status_summary = ponder_status_summary

                decision.ponder_cache_used = ponder_cache_used
                decision.ponder_cache_hit = ponder_cache_hit
                decision.ponder_used_budget_ms = ponder_used_budget_ms
                decision.ponder_fallback_reason = ponder_fallback_reason
                decision.ponder_reuse_score = ponder_reuse_score
                decision.ponder_cache_age_ms = ponder_cache_age_ms
                decision.ponder_cache_gate_reason = ponder_cache_gate_reason
                decision.ponder_gate_learned_adjustment_used = ponder_gate_learned_adjustment_used
                decision.ponder_gate_adjustment_delta = ponder_gate_adjustment_delta
                decision.ponder_gate_adjustment_source = ponder_gate_adjustment_source

                if is_ponder and self.config.swindle_ponder_enable:
                    self._store_ponder_cache(
                        self.position_state.raw_position,
                        outcome.info_result,
                        decision=decision,
                        elapsed_ms=ponder_used_budget_ms,
                    )

                final_bestmove = normal_bestmove
                selected_reason = decision.selected_reason

                if activate and not self.config.swindle_dry_run:
                    final_bestmove = self._normalize_bestmove(decision.selected_move)
                    if final_bestmove == "resign":
                        final_bestmove = normal_bestmove
                elif is_ponder and ponder_fallback_reason is not None:
                    final_bestmove = normal_bestmove
                    selected_reason = "ponder_fallback"

                if self.config.swindle_dry_run:
                    selected_reason = "dryrun_backend"

            if pre_events and not any(evt in decision.events for evt in pre_events):
                decision.events = [*pre_events, *decision.events]
        except Exception as exc:
            self._dbg(f"go fallback due to exception: {exc}")
            decision.events.append("go_exception_fallback")
            if is_ponder:
                decision.ponder_status_summary = "error"
            final_bestmove = normal_bestmove
            selected_reason = "fallback_backend"

        final_bestmove = self._normalize_bestmove(final_bestmove)
        if final_bestmove == "resign" and normal_bestmove != "resign":
            final_bestmove = normal_bestmove

        if ponder_cache_used:
            reuse_meta = self._last_ponder_reuse_meta or {}
            predicted = reuse_meta.get("predicted_move")
            ponder_reuse_decision_id = reuse_meta.get("decision_id")
            ponder_reuse_parent_position_key = reuse_meta.get("parent_position_key")
            if (
                isinstance(predicted, str)
                and is_usi_move_token(predicted)
                and is_usi_move_token(final_bestmove)
            ):
                reuse_then_bestmove_changed = predicted != final_bestmove
                ponder_label_source = "runtime_observed"
                if ponder_reuse_decision_id and ponder_reuse_parent_position_key:
                    ponder_label_confidence = 0.9
                else:
                    ponder_label_confidence = 0.6
            else:
                reuse_then_bestmove_changed = False
                ponder_label_source = "heuristic"
                ponder_label_confidence = 0.3
        elif decision.ponder_cache_hit:
            ponder_label_source = "heuristic"
            ponder_label_confidence = 0.3

        decision.reuse_then_bestmove_changed = reuse_then_bestmove_changed
        decision.ponder_reuse_decision_id = ponder_reuse_decision_id
        decision.ponder_reuse_parent_position_key = ponder_reuse_parent_position_key
        decision.ponder_label_source = ponder_label_source
        decision.ponder_label_confidence = max(0.0, min(1.0, float(ponder_label_confidence)))

        self._emit_decision_info(activate, decision, context)
        self._emit_log(
            decision=decision,
            context=context,
            go_time_info=time_info,
            search_id=outcome.search_id if outcome is not None else 0,
            normal_bestmove=normal_bestmove,
            final_bestmove=final_bestmove,
            selected_reason=selected_reason,
            backend_restart_count=max(0, self.backend_restart_count_total - restart_before),
        )

        line = f"bestmove {final_bestmove}"
        if (
            outcome is not None
            and outcome.backend_ponder
            and final_bestmove == normal_bestmove
            and is_usi_move_token(outcome.backend_ponder)
        ):
            line += f" ponder {outcome.backend_ponder}"
        self._out(line)
        self._remember_pending_feedback(
            final_bestmove=final_bestmove,
            decision=decision,
            is_ponder=is_ponder,
        )

        if outcome is not None:
            merged_deferred = list(outcome.deferred_commands) + list(decision.deferred_commands)
            self._process_deferred(merged_deferred)

    def _emit_decision_info(self, activate: bool, decision: Stage1Decision, context: SwindleContext) -> None:
        if not self.config.swindle_verbose_info:
            return

        mode = decision.mode if activate else context.mode
        requested_mode = decision.mode_requested if activate else context.mode
        mode_label = f"{requested_mode}->{mode}" if requested_mode == "AUTO" else mode
        state = "ON" if activate else "OFF"
        dry = "true" if self.config.swindle_dry_run else "false"
        emergency = "true" if context.emergency_fast_mode else "false"
        ponder = decision.ponder_status_summary
        self._info(
            f"SWINDLE {state} MODE={mode_label} DRYRUN={dry} EMERGENCY_FAST={emergency} PONDER={ponder} rootEval={context.root_eval_cp} cap={context.dynamic_drop_cap_cp}",
            level=1,
        )

        if decision.mate_detected:
            self._info("MATE DETECTED", level=1)

        if decision.option_restore_failed or any(evt.startswith("restore_failed:") for evt in decision.events):
            self._info("restore_failed:MultiPV", level=1)

        if self.config.swindle_show_ranking and decision.candidates:
            top_n = min(4, len(decision.candidates))
            for idx in range(top_n):
                c = decision.candidates[idx]
                gate = "REJECT" if c.gate_rejected else "OK"
                mate_part = f"mate={c.mate_score:+d}" if c.mate_score is not None else "mate=none"
                b = c.rev_breakdown
                short = f"m={b.mate:.0f} o={b.onlymove:.0f} e={b.entropy:.0f} t={b.trap:.0f} r=-{b.risk_penalty:.0f}"
                self._info(
                    f"rank{idx + 1}: {c.move} REV={c.rev_score:.1f} gap12={c.features.gap12:.1f} {mate_part} {short} gate={gate}",
                    level=2,
                )

        for evt in decision.events[:4]:
            if evt.startswith("VERIFY") or evt.startswith("PSEUDO_HISSHI") or evt.startswith("BACKEND") or evt.startswith("PONDER") or evt.startswith("ponder_") or evt.startswith("restore_failed:"):
                self._info(evt, level=2)
        if (not decision.ponder_cache_used) and decision.ponder_cache_gate_reason:
            self._info(f"PONDER gate={decision.ponder_cache_gate_reason}", level=3)
        if decision.ponder_reuse_score is not None:
            self._info(
                f"PONDER reuse_score={decision.ponder_reuse_score:.2f} age_ms={decision.ponder_cache_age_ms}",
                level=3,
            )
        if decision.ponder_gate_adjustment_source != "none":
            self._info(
                f"PONDER learned delta={decision.ponder_gate_adjustment_delta:+.3f} src={decision.ponder_gate_adjustment_source}",
                level=3,
            )
        if activate and decision.verify_mode_used:
            self._info(
                f"VERIFY mode={decision.verify_mode_used} engine={decision.verify_engine_kind} n={decision.mate_verify_candidates_count}",
                level=2,
            )
            if decision.verify_conflict_count > 0 or decision.verify_unknown_count > 0:
                self._info(
                    f"VERIFY HYBRID conflict={decision.verify_conflict_count} unknown={decision.verify_unknown_count}",
                    level=2,
                )
            if decision.hybrid_learned_adjustment_used:
                self._info(
                    f"VERIFY HYBRID learned delta={decision.hybrid_adjustment_delta:+.3f} src={decision.hybrid_adjustment_source}",
                    level=3,
                )
            self._info(
                f"DFPN parser={self.config.swindle_dfpn_parser_mode} dialect={decision.dfpn_dialect_used}",
                level=3,
            )
            self._info(
                f"DFPN pack={decision.dfpn_pack_source} v={decision.dfpn_pack_version} err={decision.dfpn_pack_load_errors}",
                level=3,
            )

    def _emit_log(
        self,
        decision: Stage1Decision,
        context: SwindleContext,
        go_time_info: dict[str, Optional[int]],
        search_id: int,
        normal_bestmove: str,
        final_bestmove: str,
        selected_reason: str,
        backend_restart_count: int = 0,
    ) -> None:
        cand_logs = []
        for c in decision.candidates:
            reply_topk = []
            for r in c.reply_topk:
                reply_topk.append(
                    {
                        "move": r.move,
                        "multipv": r.multipv,
                        "pv": list(r.pv),
                        "cp_raw": r.cp_raw,
                        "mate_raw": r.mate_raw,
                        "opp_utility": r.opp_utility,
                        "root_cp": r.root_cp,
                        "root_mate": r.root_mate,
                        "is_check_like": r.is_check_like,
                        "is_flashy_like": r.is_flashy_like,
                    }
                )
            cand_logs.append(
                DecisionCandidateRecord(
                    move=c.move,
                    base_cp=c.base_cp,
                    mate=c.mate_score,
                    depth=c.depth,
                    features=asdict(c.features),
                    reply_topk=reply_topk,
                    gap12=c.features.gap12,
                    gap13=c.features.gap13,
                    reply_entropy=c.features.reply_entropy,
                    pseudo_hisshi_score=c.features.pseudo_hisshi_score,
                    rev_breakdown=asdict(c.rev_breakdown),
                    rev_score=c.rev_score,
                    gate_rejected=c.gate_rejected,
                    gate_reason=c.gate_reason,
                    mate_verify_status=c.mate_verify_status,
                )
            )

        event = DecisionEvent(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            game_id=self.position_state.game_id,
            ply=self.position_state.ply,
            root_sfen=self.position_state.root_sfen,
            root_eval_cp=context.root_eval_cp,
            root_mate=context.root_mate_score,
            swindle_enabled=self.config.swindle_enable,
            mode=decision.mode,
            time_info=go_time_info,
            normal_bestmove=normal_bestmove,
            final_bestmove=final_bestmove,
            candidates=cand_logs,
            selected_reason=selected_reason,
            backend_engine_info={
                "path": self.config.backend_engine_path,
                "args": self.config.backend_engine_args,
                "name": self.backend_id_name,
                "author": self.backend_id_author,
            },
            emergency_fast_mode=context.emergency_fast_mode,
            events=list(decision.events),
            option_restore_failed=decision.option_restore_failed,
            mate_verify_status=decision.mate_verify_status,
            verify_status_summary=decision.verify_status_summary,
            verify_mode_used=decision.verify_mode_used,
            verify_engine_kind=decision.verify_engine_kind,
            mate_verify_candidates_count=decision.mate_verify_candidates_count,
            dfpn_used=decision.dfpn_used,
            dfpn_status_summary=decision.dfpn_status_summary,
            dfpn_parser_hits=list(decision.dfpn_parser_hits),
            dfpn_parse_unknown_count=decision.dfpn_parse_unknown_count,
            dfpn_distance_available_count=decision.dfpn_distance_available_count,
            dfpn_dialect_used=decision.dfpn_dialect_used,
            dfpn_dialect_candidates=list(decision.dfpn_dialect_candidates),
            dfpn_source_detail_normalized=decision.dfpn_source_detail_normalized,
            dfpn_pack_source=decision.dfpn_pack_source,
            dfpn_pack_version=decision.dfpn_pack_version,
            dfpn_pack_load_errors=decision.dfpn_pack_load_errors,
            verify_conflict_count=decision.verify_conflict_count,
            verify_unknown_count=decision.verify_unknown_count,
            dfpn_parser_mode=self.config.swindle_dfpn_parser_mode,
            verify_hybrid_policy=self.config.swindle_verify_hybrid_policy,
            hybrid_learned_adjustment_used=decision.hybrid_learned_adjustment_used,
            hybrid_adjustment_delta=decision.hybrid_adjustment_delta,
            hybrid_adjustment_source=decision.hybrid_adjustment_source,
            pseudo_hisshi_status=decision.pseudo_hisshi_status,
            ponder_status_summary=decision.ponder_status_summary,
            ponder_cache_used=decision.ponder_cache_used,
            ponder_cache_hit=decision.ponder_cache_hit,
            ponder_used_budget_ms=decision.ponder_used_budget_ms,
            ponder_fallback_reason=decision.ponder_fallback_reason,
            ponder_reuse_score=decision.ponder_reuse_score,
            ponder_cache_age_ms=decision.ponder_cache_age_ms,
            ponder_cache_gate_reason=decision.ponder_cache_gate_reason,
            ponder_gate_learned_adjustment_used=decision.ponder_gate_learned_adjustment_used,
            ponder_gate_adjustment_delta=decision.ponder_gate_adjustment_delta,
            ponder_gate_adjustment_source=decision.ponder_gate_adjustment_source,
            reuse_then_bestmove_changed=decision.reuse_then_bestmove_changed,
            ponder_reuse_decision_id=decision.ponder_reuse_decision_id,
            ponder_reuse_parent_position_key=decision.ponder_reuse_parent_position_key,
            ponder_label_source=decision.ponder_label_source,
            ponder_label_confidence=decision.ponder_label_confidence,
            backend_restart_count=backend_restart_count,
            dfpn_status=decision.dfpn_status_summary,
            actual_opponent_move=self._feedback_actual_opponent_move,
            actual_move_in_reply_topk=self._feedback_actual_move_in_reply_topk,
            actual_move_rank_in_reply_topk=self._feedback_actual_move_rank_in_reply_topk,
            outcome_tag=self._feedback_outcome_tag,
            outcome_confidence=self._feedback_outcome_confidence,
            dry_run=self.config.swindle_dry_run,
            search_id=search_id,
        )
        self.logger.log_decision(event)
        self._clear_feedback_annotations()

    def _process_deferred(self, deferred: list[str]) -> None:
        for cmd in deferred:
            if cmd.startswith("setoption"):
                self._handle_setoption(cmd)
            elif cmd.startswith("position"):
                self._handle_position(cmd)
            elif cmd == "usinewgame":
                self._handle_usinewgame()
            elif cmd.startswith("go"):
                self.stdin_reader.put_back(cmd)
            elif cmd == "quit":
                self.quit_requested = True
            else:
                self._forward_to_backend(cmd)

    def _remember_pending_feedback(self, *, final_bestmove: str, decision: Stage1Decision, is_ponder: bool) -> None:
        if is_ponder:
            return
        if not is_usi_move_token(final_bestmove):
            self._pending_feedback = None
            return

        reply_moves: list[str] = []
        for cand in decision.candidates:
            if cand.move != final_bestmove:
                continue
            reply_moves = [r.move for r in cand.reply_topk if r.move]
            break

        self._pending_feedback = {
            "base_moves": list(self.position_state.moves),
            "selected_move": final_bestmove,
            "reply_topk_moves": reply_moves,
        }

    def _update_feedback_from_position(self, position_cmd: str) -> None:
        pending = self._pending_feedback
        if not pending:
            return

        new_moves = self._extract_moves_from_position(position_cmd)
        base_moves = pending.get("base_moves")
        selected_move = pending.get("selected_move")
        reply_topk_moves = pending.get("reply_topk_moves")
        if not isinstance(base_moves, list) or not isinstance(selected_move, str):
            self._pending_feedback = None
            return
        if not isinstance(reply_topk_moves, list):
            reply_topk_moves = []

        base_len = len(base_moves)
        if len(new_moves) <= base_len:
            return

        if new_moves[base_len] != selected_move:
            self._pending_feedback = None
            return

        if len(new_moves) <= base_len + 1:
            return

        actual_move = new_moves[base_len + 1]
        rank: Optional[int] = None
        for idx, move in enumerate(reply_topk_moves):
            if isinstance(move, str) and move == actual_move:
                rank = idx + 1
                break

        self._feedback_actual_opponent_move = actual_move
        self._feedback_actual_move_in_reply_topk = rank is not None
        self._feedback_actual_move_rank_in_reply_topk = rank
        self._feedback_outcome_tag = None
        self._feedback_outcome_confidence = None
        self._pending_feedback = None

    def _extract_moves_from_position(self, position_cmd: str) -> list[str]:
        tokens = position_cmd.split()
        if not tokens or tokens[0] != "position":
            return []
        if "moves" not in tokens:
            return []
        idx = tokens.index("moves")
        return tokens[idx + 1 :]

    def _position_key(self, position_cmd: str) -> str:
        return " ".join((position_cmd or "").strip().split())

    def _clear_feedback_annotations(self) -> None:
        self._feedback_actual_opponent_move = None
        self._feedback_actual_move_in_reply_topk = None
        self._feedback_actual_move_rank_in_reply_topk = None
        self._feedback_outcome_tag = None
        self._feedback_outcome_confidence = None

    def _store_ponder_cache(
        self,
        position_cmd: str,
        info_result: InfoParseResult,
        *,
        decision: Optional[Stage1Decision] = None,
        elapsed_ms: int = 0,
    ) -> None:
        root_eval_cp: Optional[int] = None
        root = info_result.by_multipv.get(1)
        if root is not None:
            root_eval_cp = root.cp

        candidate_count = 0
        top_gap12: Optional[float] = None
        had_mate_signal = False
        reply_coverage = 0.0
        verify_done = False
        predicted_move: Optional[str] = None
        decision_id: Optional[str] = None
        parent_position_key = self._position_key(position_cmd)
        if decision is not None:
            candidate_count = len(decision.candidates)
            if decision.candidates:
                top_gap12 = decision.candidates[0].features.gap12
                coverages: list[float] = []
                target_topk = max(1, int(getattr(self.config, "swindle_reply_topk", 4)))
                for cand in decision.candidates:
                    if cand.mate_score is not None:
                        had_mate_signal = True
                    if cand.reply_topk:
                        coverages.append(min(1.0, len(cand.reply_topk) / float(target_topk)))
                if decision.mate_detected:
                    had_mate_signal = True
                if coverages:
                    reply_coverage = sum(coverages) / float(len(coverages))
            verify_done = decision.verify_status_summary not in {"not_used", "skipped"}
            predicted_raw = decision.selected_move or decision.normal_bestmove
            normalized = self._normalize_bestmove(predicted_raw)
            if is_usi_move_token(normalized):
                predicted_move = normalized
            decision_id = f"ponder:{int(time.time() * 1000)}:{self.position_state.game_id}:{self.position_state.ply}"

        self._ponder_cache = PonderCacheEntry(
            position_cmd=position_cmd,
            info_result=self._clone_info_result(info_result),
            created_ts=time.time(),
            root_eval_cp=root_eval_cp,
            candidate_count=candidate_count,
            top_gap12=top_gap12,
            had_mate_signal=had_mate_signal,
            elapsed_ms=max(0, int(elapsed_ms)),
            reply_coverage=max(0.0, min(1.0, float(reply_coverage))),
            verify_done=verify_done,
            predicted_move=predicted_move,
            decision_id=decision_id,
            parent_position_key=parent_position_key,
        )

    def _consume_ponder_cache(
        self,
        position_cmd: str,
        *,
        max_age_sec: float,
    ) -> tuple[Optional[InfoParseResult], bool, bool, Optional[float], int, Optional[str]]:
        self._reset_last_ponder_gate_adjustment()
        self._last_ponder_reuse_meta = None
        entry = self._ponder_cache
        self._ponder_cache = None
        if entry is None:
            return None, False, False, None, 0, None
        age_ms = int(max(0.0, (time.time() - entry.created_ts) * 1000.0))
        max_age_ms = int(max(0.0, max_age_sec * 1000.0))
        if age_ms > max_age_ms:
            return None, False, True, 0.0, age_ms, "stale"
        if entry.position_cmd.strip() != position_cmd.strip():
            return None, False, True, 0.0, age_ms, "position_miss"

        reuse_score, gate_reason = self._ponder_reuse_score(entry=entry, age_ms=age_ms)
        if gate_reason is None:
            reuse_score = self._apply_ponder_learned_adjustment(entry=entry, age_ms=age_ms, base_score=reuse_score)

        min_score = max(0.0, min(1.0, float(getattr(self.config, "swindle_ponder_reuse_min_score", 55)) / 100.0))
        if reuse_score < min_score:
            reason = gate_reason or "quality_gate"
            return None, True, True, reuse_score, age_ms, reason
        if gate_reason is not None:
            return None, True, True, reuse_score, age_ms, gate_reason

        self._last_ponder_reuse_meta = {
            "predicted_move": entry.predicted_move,
            "decision_id": entry.decision_id,
            "parent_position_key": entry.parent_position_key or self._position_key(entry.position_cmd),
        }
        return self._clone_info_result(entry.info_result), True, True, reuse_score, age_ms, None

    def _apply_ponder_learned_adjustment(self, *, entry: PonderCacheEntry, age_ms: int, base_score: float) -> float:
        if not bool(getattr(self.config, "swindle_use_ponder_gate_learned_adjustment", False)):
            return max(0.0, min(1.0, base_score))

        tuner = getattr(self.controller, "weight_tuner", None)
        adjust_fn = getattr(tuner, "get_ponder_gate_adjustment", None)
        if not callable(adjust_fn):
            return max(0.0, min(1.0, base_score))

        features = {
            "reply_coverage": entry.reply_coverage,
            "candidate_count": entry.candidate_count,
            "top_gap12": entry.top_gap12,
            "had_mate_signal": entry.had_mate_signal,
            "elapsed_ms": entry.elapsed_ms,
            "cache_age_ms": age_ms,
            "max_age_ms": int(getattr(self.config, "swindle_ponder_cache_max_age_ms", 3000)),
            "verify_done_for_mate_cache": entry.verify_done,
            # Offline builder may optionally provide this; default to stable behavior.
            "reuse_then_bestmove_changed": False,
        }

        try:
            delta, source, used = adjust_fn(
                features,
                cap_pct=float(getattr(self.config, "swindle_ponder_reuse_learned_adjustment_cap_pct", 20)),
                require_feature_version_match=True,
            )
        except Exception:
            return max(0.0, min(1.0, base_score))

        self._last_ponder_gate_adjustment_used = bool(used)
        self._last_ponder_gate_adjustment_delta = float(delta)
        self._last_ponder_gate_adjustment_source = str(source or "none")
        return max(0.0, min(1.0, base_score + float(delta)))

    def _ponder_reuse_score(self, *, entry: PonderCacheEntry, age_ms: int) -> tuple[float, Optional[str]]:
        score = 0.0
        score += max(0.0, min(1.0, entry.reply_coverage)) * 0.35
        score += min(1.0, max(0.0, float(entry.candidate_count) / 4.0)) * 0.20
        if entry.top_gap12 is not None:
            score += min(1.0, max(0.0, float(entry.top_gap12)) / 800.0) * 0.20

        if entry.elapsed_ms >= 120:
            score += 0.15
        elif entry.elapsed_ms >= 40:
            score += 0.08
        else:
            score -= 0.10

        max_age_ms = max(1, int(getattr(self.config, "swindle_ponder_cache_max_age_ms", 3000)))
        age_ratio = float(age_ms) / float(max_age_ms)
        if age_ratio <= 0.35:
            score += 0.10
        elif age_ratio <= 0.70:
            score += 0.05
        else:
            score -= 0.05

        require_verify = bool(getattr(self.config, "swindle_ponder_require_verify_for_mate_cache", True))
        if entry.had_mate_signal and require_verify and not entry.verify_done:
            score -= 0.35
            return max(0.0, min(1.0, score)), "mate_without_verify"

        return max(0.0, min(1.0, score)), None

    def _merge_info_results(self, live: InfoParseResult, cached: InfoParseResult) -> InfoParseResult:
        merged = self._clone_info_result(live)
        for snap in cached.by_move.values():
            merged.upsert(self._clone_snapshot(snap))
        return merged

    def _clone_info_result(self, src: InfoParseResult) -> InfoParseResult:
        out = InfoParseResult()
        for snap in src.by_move.values():
            out.upsert(self._clone_snapshot(snap))
        for mpv, snap in src.by_multipv.items():
            if mpv not in out.by_multipv:
                out.by_multipv[mpv] = self._clone_snapshot(snap)
        return out

    def _clone_snapshot(self, snap: InfoSnapshot) -> InfoSnapshot:
        return InfoSnapshot(
            multipv=snap.multipv,
            depth=snap.depth,
            seldepth=snap.seldepth,
            cp=snap.cp,
            mate=snap.mate,
            nodes=snap.nodes,
            nps=snap.nps,
            hashfull=snap.hashfull,
            time_ms=snap.time_ms,
            pv=list(snap.pv),
            move=snap.move,
            raw_line=snap.raw_line,
            timestamp=snap.timestamp,
        )

    def _forward_to_backend(self, line: str) -> None:
        if not self._ensure_engine_started():
            return
        if not self._initialize_backend_usi():
            return
        assert self.engine is not None
        self.engine.send(line)

    def _ensure_engine_started(self) -> bool:
        if self.engine is not None and self.engine.alive and not self.restart_backend_required:
            return True

        had_live = self.engine is not None
        if self.engine is not None:
            self.engine.close()

        cmd = self._build_engine_command()
        try:
            self.engine = EngineProcess(command=cmd, cwd=os.getcwd(), encoding=self.config.encoding)
            self.engine.start()
            self.engine_session = EngineSession(self.engine, self.config)
            self.backend_initialized = False
            self.backend_supported_options.clear()
            self.backend_option_lines = []
            self.restart_backend_required = False
            if had_live:
                self.backend_restart_count_total += 1
            return True
        except FileNotFoundError:
            self._info(f"backend not found: {cmd.executable}", level=1)
            self.engine = None
            self.engine_session = None
            return False
        except Exception:
            self.engine = None
            self.engine_session = None
            return False

    def _build_engine_command(self) -> EngineCommand:
        exe = self.config.backend_engine_path.strip()
        if exe and not os.path.isabs(exe):
            exe = os.path.abspath(exe)
        return EngineCommand(executable=exe, args=self.config.backend_engine_args)

    def _initialize_backend_usi(self) -> bool:
        if self.backend_initialized:
            return True
        if self.engine is None:
            return False

        self.backend_supported_options.clear()
        self.backend_option_lines = []
        self.backend_id_name = ""
        self.backend_id_author = ""

        self.engine.drain()
        self.engine.send("usi")

        deadline = time.time() + self.config.usi_init_timeout_sec
        while time.time() < deadline:
            line = self.engine.recv(self.config.read_timeout)
            if line is None:
                continue

            if line.startswith("id name "):
                self.backend_id_name = line[len("id name ") :].strip()
                continue
            if line.startswith("id author "):
                self.backend_id_author = line[len("id author ") :].strip()
                continue
            if line.startswith("option "):
                name = parse_option_name(line)
                if name:
                    self.backend_supported_options.add(name)
                self.backend_option_lines.append(line)
                continue
            if line == "usiok":
                self.backend_initialized = True
                break

        if not self.backend_initialized:
            self._info("backend usi initialization timeout", level=1)

        return self.backend_initialized

    def _wait_backend_ready(self) -> None:
        if self.engine is None:
            return

        self.engine.send("isready")
        deadline = time.time() + self.config.isready_timeout_sec
        while time.time() < deadline:
            line = self.engine.recv(self.config.read_timeout)
            if line is None:
                continue
            if line == "readyok":
                return

        self._info("backend ready timeout", level=1)

    def _apply_backend_options(self) -> None:
        if self.engine is None:
            return

        passthrough = self.config.parse_backend_option_passthrough()
        for name, value in passthrough.items():
            if name in self.backend_supported_options:
                self.engine.send(f"setoption name {name} value {value}")

        if "MultiPV" in self.backend_supported_options:
            self.engine.send(f"setoption name MultiPV value {self.config.swindle_multipv}")

    def _should_activate_swindle(self, context: SwindleContext) -> bool:
        if not self.config.swindle_enable:
            return False

        if self.config.swindle_force_at_mate_loss and context.root_mate_score is not None and context.root_mate_score < 0:
            return True

        if context.root_eval_cp is None:
            return True

        return context.root_eval_cp <= self.config.swindle_eval_threshold_cp

    def _normalize_bestmove(self, move: Optional[str]) -> str:
        if move is None:
            return "resign"
        if is_special_bestmove(move):
            return move
        if is_usi_move_token(move):
            return move
        return "resign"

    def _parse_go_time_info(self, go_line: str) -> dict[str, Optional[int]]:
        tokens = go_line.split()
        fields = {
            "btime": None,
            "wtime": None,
            "byoyomi": None,
            "binc": None,
            "winc": None,
            "movetime": None,
            "movestogo": None,
        }

        for key in list(fields.keys()):
            if key in tokens:
                i = tokens.index(key)
                if i + 1 < len(tokens):
                    try:
                        fields[key] = int(tokens[i + 1])
                    except Exception:
                        fields[key] = None
        return fields

    def _shutdown(self) -> None:
        try:
            self.mate_adapter.close()
        except Exception:
            pass

        if self.engine is not None:
            self.engine.close()

    def _reset_last_ponder_gate_adjustment(self) -> None:
        self._last_ponder_gate_adjustment_used = False
        self._last_ponder_gate_adjustment_delta = 0.0
        self._last_ponder_gate_adjustment_source = "none"

    def _sync_ponder_gate_weights_config(self) -> None:
        tuner = getattr(self.controller, "weight_tuner", None)
        load_fn = getattr(tuner, "load_ponder_gate_weights", None)
        if not callable(load_fn):
            return
        path = str(getattr(self.config, "swindle_ponder_gate_weights_path", "") or "").strip()
        use_learned = bool(getattr(self.config, "swindle_use_ponder_gate_learned_adjustment", False))
        key = f"{int(use_learned)}:{path}"
        if key == self._ponder_gate_weights_loaded_path:
            return
        self._ponder_gate_weights_loaded_path = key
        try:
            if use_learned and path:
                load_fn(path)
            else:
                load_fn("")
        except Exception:
            pass

    def _sync_mate_adapter_config(self) -> None:
        try:
            self.mate_adapter.configure_fallback(
                backend_engine_path=self.config.backend_engine_path,
                backend_engine_args=self.config.backend_engine_args,
                backend_option_passthrough=self.config.backend_engine_option_passthrough,
            )
            self.mate_adapter.configure_runtime(
                mate_engine_path=(self.config.swindle_mate_engine_path or self.config.mate_engine_path),
                mate_engine_eval_dir=self.config.swindle_mate_engine_eval_dir,
                mate_engine_profile=self.config.swindle_mate_engine_profile,
                verify_mode=self.config.swindle_verify_mode,
                verify_aggressive_extra_ms=self.config.swindle_verify_aggressive_extra_ms,
                verify_hybrid_policy=self.config.swindle_verify_hybrid_policy,
                use_dfpn=self.config.swindle_use_dfpn,
                dfpn_path=self.config.swindle_dfpn_path,
                dfpn_time_ms=self.config.swindle_dfpn_time_ms,
                dfpn_parser_mode=self.config.swindle_dfpn_parser_mode,
                dfpn_dialect=self.config.swindle_dfpn_dialect,
                dfpn_dialect_pack_path=self.config.swindle_dfpn_dialect_pack_path,
                use_hybrid_learned_adjustment=self.config.swindle_use_hybrid_learned_adjustment,
                hybrid_weights_path=self.config.swindle_hybrid_weights_path,
                hybrid_adjustment_cap_pct=self.config.swindle_hybrid_adjustment_cap_pct,
                hybrid_label_mode=self.config.swindle_hybrid_label_mode,
                hybrid_require_feature_version_match=self.config.swindle_hybrid_require_feature_version_match,
            )
        except Exception:
            pass
