from __future__ import annotations

import os
import time
from typing import Optional

from ..config import SwindleConfig
from ..engine_process import EngineCommand, EngineProcess
from ..engine_session import EngineSession, QueueReadable
from ..swindle.weight_tuner import HYBRID_FEATURES_VERSION, WeightTuner
from ..usi_messages import parse_option_name
from .dfpn_adapter import DfPnAdapter
from .mate_result import MateResult

VERIFY_ONLY = "VERIFY_ONLY"
VERIFY_TOP = "TOP_CANDIDATES"
VERIFY_AGGRESSIVE = "AGGRESSIVE"

HYBRID_CONSERVATIVE = "CONSERVATIVE"
HYBRID_BALANCED = "BALANCED"
HYBRID_MATE_ENGINE_FIRST = "MATE_ENGINE_FIRST"
HYBRID_DFPN_FIRST = "DFPN_FIRST"

PROFILE_AUTO = "AUTO"
PROFILE_SAFE = "SAFE"
PROFILE_FAST_VERIFY = "FAST_VERIFY"


class MateAdapter:
    """
    Mate verification adapter with reusable verifier process.

    Rules:
    - Lazy start verifier.
    - Keep process alive and reuse.
    - Restart only on dead/unhealthy states.
    - Dedicated mate engine preferred; fallback to backend verifier.
    """

    def __init__(self, mate_engine_path: str = "") -> None:
        self.mate_engine_path = mate_engine_path.strip()
        self.mate_engine_eval_dir = ""
        self.mate_engine_profile = PROFILE_AUTO
        self.verify_mode = VERIFY_ONLY
        self.verify_aggressive_extra_ms = 0

        self.use_dfpn = False
        self.dfpn_time_ms = 120
        self.dfpn_parser_mode = "AUTO"
        self.dfpn_dialect = "AUTO"
        self.verify_hybrid_policy = HYBRID_CONSERVATIVE
        self.use_hybrid_learned_adjustment = False
        self.hybrid_weights_path = ""
        self.hybrid_adjustment_cap_pct = 15
        self.hybrid_label_mode = "PSEUDO"
        self.hybrid_require_feature_version_match = True
        self._hybrid_weights_loaded_path = ""
        self._weight_tuner = WeightTuner()
        self.dfpn_adapter = DfPnAdapter("")

        self._fallback_backend_path = ""
        self._fallback_backend_args = ""
        self._backend_option_passthrough = ""

        self._engine: Optional[EngineProcess] = None
        self._session: Optional[EngineSession] = None
        self._config = SwindleConfig()
        self._config.read_timeout = 0.05
        self._config.go_hard_sec = 0.8
        self._config.go_stop_grace_sec = 0.2
        self._config.go_hard_sec_infinite = 0.0
        self._config.isready_timeout_sec = 1.8
        self._config.usi_init_timeout_sec = 1.8

        self._supported_options: set[str] = set()
        self._initialized = False
        self._engine_kind_running = "backend"
        self._last_ready_ok_ts = 0.0
        self._consecutive_health_failures = 0
        self._last_command_signature: tuple[str, str, str] = ("", "", "")

    def configure_fallback(
        self,
        *,
        backend_engine_path: str,
        backend_engine_args: str = "",
        backend_option_passthrough: str = "",
    ) -> None:
        self._fallback_backend_path = backend_engine_path.strip()
        self._fallback_backend_args = backend_engine_args.strip()
        self._backend_option_passthrough = backend_option_passthrough.strip()

    def configure_runtime(
        self,
        *,
        mate_engine_path: str = "",
        mate_engine_eval_dir: str = "",
        mate_engine_profile: str = PROFILE_AUTO,
        verify_mode: str = VERIFY_ONLY,
        verify_aggressive_extra_ms: int = 0,
        verify_hybrid_policy: str = HYBRID_CONSERVATIVE,
        use_dfpn: bool = False,
        dfpn_path: str = "",
        dfpn_time_ms: int = 120,
        dfpn_parser_mode: str = "AUTO",
        dfpn_dialect: str = "AUTO",
        dfpn_dialect_pack_path: str = "",
        use_hybrid_learned_adjustment: bool = False,
        hybrid_weights_path: str = "",
        hybrid_adjustment_cap_pct: int = 15,
        hybrid_label_mode: str = "PSEUDO",
        hybrid_require_feature_version_match: bool = False,
    ) -> None:
        self.mate_engine_path = mate_engine_path.strip()
        self.mate_engine_eval_dir = mate_engine_eval_dir.strip()
        self.mate_engine_profile = _normalize_profile(mate_engine_profile)
        self.verify_mode = _normalize_verify_mode(verify_mode)
        self.verify_aggressive_extra_ms = max(0, int(verify_aggressive_extra_ms))
        self.verify_hybrid_policy = _normalize_hybrid_policy(verify_hybrid_policy)
        self.use_dfpn = bool(use_dfpn)
        self.dfpn_time_ms = max(1, int(dfpn_time_ms))
        self.dfpn_parser_mode = _normalize_parser_mode(dfpn_parser_mode)
        self.dfpn_dialect = _normalize_dialect(dfpn_dialect)
        self.dfpn_adapter.configure(
            path=dfpn_path.strip(),
            parser_mode=self.dfpn_parser_mode,
            dialect=self.dfpn_dialect,
            dialect_pack_path=dfpn_dialect_pack_path.strip(),
        )
        self.use_hybrid_learned_adjustment = bool(use_hybrid_learned_adjustment)
        self.hybrid_weights_path = hybrid_weights_path.strip()
        self.hybrid_adjustment_cap_pct = max(0, min(50, int(hybrid_adjustment_cap_pct)))
        self.hybrid_label_mode = (hybrid_label_mode or "PSEUDO").strip().upper() or "PSEUDO"
        self.hybrid_require_feature_version_match = bool(hybrid_require_feature_version_match)
        if self.hybrid_weights_path != self._hybrid_weights_loaded_path:
            self._weight_tuner.load_hybrid_weights(self.hybrid_weights_path)
            self._hybrid_weights_loaded_path = self.hybrid_weights_path

    def verify(
        self,
        sfen: str,
        move: str,
        timeout_ms: int,
        *,
        mode: str = VERIFY_ONLY,
        root_position_cmd: Optional[str] = None,
    ) -> MateResult:
        notes: list[str] = []
        verify_mode = _normalize_verify_mode(mode if mode else self.verify_mode)

        if timeout_ms <= 0:
            notes.append("timeout_ms<=0")
            return MateResult(
                found_mate=False,
                status="timeout",
                source="verify",
                engine_kind="backend",
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )
        if not self.available():
            notes.append("engine_unavailable")
            return MateResult(
                found_mate=False,
                status="skipped",
                source="verify",
                engine_kind="backend",
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )
        if not move:
            notes.append("empty_move")
            return MateResult(
                found_mate=False,
                status="skipped",
                source="verify",
                engine_kind="backend",
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )

        eff_timeout = max(20, int(timeout_ms))

        if not self._ensure_verifier(eff_timeout, notes):
            return MateResult(
                found_mate=False,
                status="error",
                source="verify",
                engine_kind="backend",
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )

        assert self._engine is not None
        assert self._session is not None

        self._config.go_hard_sec = max(0.05, (eff_timeout / 1000.0) + 0.08)
        self._config.go_stop_grace_sec = min(0.30, max(0.05, eff_timeout / 2000.0))

        position_cmd = self._build_position_cmd(root_position_cmd, sfen, move)
        if not position_cmd:
            notes.append("invalid_position_cmd")
            return MateResult(
                found_mate=False,
                status="error",
                source="verify",
                engine_kind=self._engine_kind_running,
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )

        self._engine.send(position_cmd)
        go_cmd = self._build_go_cmd(eff_timeout, verify_mode)
        outcome = self._session.run_go(go_cmd, _NullStdinReader())

        if outcome.backend_dead:
            notes.append("backend_dead")
            self._consecutive_health_failures += 1
            if self._consecutive_health_failures >= 1:
                self._restart_verifier(notes)
            return MateResult(
                found_mate=False,
                status="error",
                source="verify",
                engine_kind=self._engine_kind_running,
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )

        if outcome.timed_out:
            notes.append("verify_timeout")
            base = MateResult(
                found_mate=False,
                status="timeout",
                source="verify",
                engine_kind=self._engine_kind_running,
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )
            return self._merge_dfpn(base, root_position_cmd or "", move)

        top = outcome.info_result.by_multipv.get(1)
        if top is None:
            notes.append("no_info")
            base = MateResult(
                found_mate=False,
                status="not_used",
                source="verify",
                engine_kind=self._engine_kind_running,
                mate_sign="unknown",
                confidence=0.0,
                notes=notes,
            )
            return self._merge_dfpn(base, root_position_cmd or "", move)

        base = self._interpret_verify_top(top_mate=top.mate, top_cp=top.cp, notes=notes)
        base.source = "verify"
        base.engine_kind = self._engine_kind_running
        return self._merge_dfpn(base, root_position_cmd or "", move)

    def available(self) -> bool:
        return self._preferred_command() is not None or self._fallback_command() is not None

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:
                pass
        self._engine = None
        self._session = None
        self._initialized = False
        self._supported_options.clear()
        self._last_ready_ok_ts = 0.0
        self._consecutive_health_failures = 0

    def _interpret_verify_top(
        self,
        *,
        top_mate: Optional[int],
        top_cp: Optional[int],
        notes: list[str],
    ) -> MateResult:
        if top_mate is not None:
            if top_mate < 0:
                mate_in = abs(int(top_mate))
                confidence = _clamp(1.0 - min(30, mate_in) / 35.0, lo=0.35, hi=0.95)
                notes.append(f"mate_for_us:{top_mate}")
                return MateResult(
                    found_mate=True,
                    mate_in=mate_in,
                    distance=mate_in,
                    confidence=confidence,
                    status="confirmed",
                    engine_kind=self._engine_kind_running,
                    mate_sign="for_us",
                    notes=list(notes),
                )
            notes.append(f"mate_for_them:{top_mate}")
            return MateResult(
                found_mate=False,
                mate_in=abs(int(top_mate)),
                distance=abs(int(top_mate)),
                confidence=0.7,
                status="rejected",
                engine_kind=self._engine_kind_running,
                mate_sign="for_them",
                notes=list(notes),
            )

        if top_cp is not None:
            root_cp = -int(top_cp)
            if root_cp <= -1800:
                notes.append(f"danger_cp:{root_cp}")
                return MateResult(
                    found_mate=False,
                    confidence=0.55,
                    status="rejected",
                    engine_kind=self._engine_kind_running,
                    mate_sign="for_them",
                    notes=list(notes),
                )
            if root_cp >= 1000:
                notes.append(f"promising_cp:{root_cp}")
        return MateResult(
            found_mate=False,
            confidence=0.0,
            status="not_used",
            engine_kind=self._engine_kind_running,
            mate_sign="unknown",
            notes=list(notes),
        )

    def _merge_dfpn(self, base: MateResult, root_position_cmd: str, move: str) -> MateResult:
        if not self.use_dfpn:
            return self._apply_learned_adjustment(
                base,
                verifier_snapshot=_clone_result(base),
                dfpn_result=_dfpn_placeholder_result(status="not_used"),
            )
        if not self.dfpn_adapter.available():
            base.notes.append("dfpn_status:skipped")
            base.notes.append("dfpn_unavailable")
            return self._apply_learned_adjustment(
                base,
                verifier_snapshot=_clone_result(base),
                dfpn_result=_dfpn_placeholder_result(status="skipped", note="dfpn_unavailable"),
            )
        if not root_position_cmd:
            base.notes.append("dfpn_status:skipped")
            base.notes.append("dfpn_no_position")
            return self._apply_learned_adjustment(
                base,
                verifier_snapshot=_clone_result(base),
                dfpn_result=_dfpn_placeholder_result(status="skipped", note="dfpn_no_position"),
            )

        dfpn = self.dfpn_adapter.verify(
            root_position_cmd=root_position_cmd,
            move=move,
            timeout_ms=self.dfpn_time_ms,
            parser_mode=self.dfpn_parser_mode,
            dialect=self.dfpn_dialect,
        )
        base.notes.append(f"dfpn_status:{dfpn.status}")
        if dfpn.source_detail:
            base.notes.append(f"dfpn_source:{dfpn.source_detail}")
        if dfpn.notes:
            base.notes.extend(dfpn.notes[:3])

        verifier_snapshot = _clone_result(base)
        merged = self._merge_hybrid(base, dfpn)
        merged.engine_kind = "hybrid"
        if dfpn.source_detail and not merged.source_detail:
            merged.source_detail = dfpn.source_detail
        if dfpn.raw_summary and not merged.raw_summary:
            merged.raw_summary = dfpn.raw_summary
        merged.dfpn_dialect_used = dfpn.dfpn_dialect_used
        merged.dfpn_dialect_candidates = list(getattr(dfpn, "dfpn_dialect_candidates", []))
        merged.dfpn_source_detail_normalized = dfpn.dfpn_source_detail_normalized or dfpn.source_detail
        merged.dfpn_pack_source = dfpn.dfpn_pack_source
        merged.dfpn_pack_version = dfpn.dfpn_pack_version
        merged.dfpn_pack_load_errors = int(getattr(dfpn, "dfpn_pack_load_errors", 0) or 0)
        return self._apply_learned_adjustment(merged, verifier_snapshot=verifier_snapshot, dfpn_result=dfpn)

    def _apply_learned_adjustment(
        self,
        merged: MateResult,
        *,
        verifier_snapshot: MateResult,
        dfpn_result: MateResult,
    ) -> MateResult:
        if not self.use_hybrid_learned_adjustment:
            merged.hybrid_learned_adjustment_used = False
            merged.hybrid_adjustment_delta = 0.0
            merged.hybrid_adjustment_source = "none"
            return merged

        if self.hybrid_weights_path != self._hybrid_weights_loaded_path:
            self._weight_tuner.load_hybrid_weights(self.hybrid_weights_path)
            self._hybrid_weights_loaded_path = self.hybrid_weights_path

        delta, source, used = self._weight_tuner.get_hybrid_adjustment(
            {
                "verifier_sign": _effective_sign(verifier_snapshot),
                "dfpn_sign": _effective_sign(dfpn_result),
                "verifier_confidence": verifier_snapshot.confidence,
                "dfpn_confidence": dfpn_result.confidence,
                "dfpn_distance": dfpn_result.distance,
                "dfpn_source_detail": dfpn_result.source_detail or "",
                "verify_mode": self.verify_mode,
                "dfpn_parser_mode": self.dfpn_parser_mode,
                "label_mode": self.hybrid_label_mode,
                "emergency_fast_mode": False,
            },
            cap_pct=float(self.hybrid_adjustment_cap_pct),
            require_feature_version_match=self.hybrid_require_feature_version_match,
            runtime_features_version=HYBRID_FEATURES_VERSION,
        )
        if not used:
            merged.hybrid_learned_adjustment_used = False
            merged.hybrid_adjustment_delta = 0.0
            merged.hybrid_adjustment_source = source
            if source == "version_mismatch":
                merged.notes.append("hybrid_adjust_version_mismatch")
            return merged

        old_conf = _clamp(merged.confidence)
        new_conf = _clamp(old_conf + delta)
        merged.confidence = new_conf
        merged.hybrid_learned_adjustment_used = True
        merged.hybrid_adjustment_delta = delta
        merged.hybrid_adjustment_source = source or "file"
        merged.notes.append(f"hybrid_adjust:{delta:+.3f}:{merged.hybrid_adjustment_source}")

        # Safe boundary modulation only around uncertain edges.
        if merged.status == "rejected" and merged.mate_sign == "for_them":
            if old_conf < 0.55 and new_conf < 0.50:
                merged.status = "unknown"
                merged.mate_sign = "unknown"
                merged.found_mate = False
                merged.notes.append("hybrid_adjust_hold_unknown")
        elif merged.status == "confirmed" and merged.mate_sign == "for_us":
            if old_conf < 0.55 and new_conf < 0.50:
                merged.status = "unknown"
                merged.mate_sign = "unknown"
                merged.found_mate = False
                merged.notes.append("hybrid_adjust_hold_unknown")
        return merged

    def _merge_hybrid(self, base: MateResult, dfpn: MateResult) -> MateResult:
        policy = self.verify_hybrid_policy
        base.notes.append(f"hybrid_policy:{policy}")
        base_sign = _effective_sign(base)
        dfpn_sign = _effective_sign(dfpn)
        base_conf = _clamp(base.confidence)
        dfpn_conf = _clamp(dfpn.confidence)

        if dfpn.status in {"timeout", "error", "skipped"}:
            base.notes.append(f"dfpn_{dfpn.status}")
            base.source = "verify+dfpn"
            return base

        if policy == HYBRID_MATE_ENGINE_FIRST:
            if base_sign != "unknown":
                if dfpn_sign != "unknown" and dfpn_sign != base_sign:
                    base.notes.append("hybrid_conflict_mate_engine_first")
                return _set_hybrid_result(base, sign=base_sign, confidence=max(base_conf, 0.45))
            if dfpn_sign != "unknown":
                base.notes.append("hybrid_use_dfpn_fallback")
                return _set_hybrid_result(base, sign=dfpn_sign, confidence=max(dfpn_conf, 0.40), distance=dfpn.distance)
            return _set_hybrid_unknown(base, note="hybrid_unknown")

        if policy == HYBRID_DFPN_FIRST:
            if dfpn_sign != "unknown":
                if base_sign != "unknown" and base_sign != dfpn_sign:
                    base.notes.append("hybrid_conflict_dfpn_first")
                return _set_hybrid_result(base, sign=dfpn_sign, confidence=max(dfpn_conf, 0.40), distance=dfpn.distance)
            if base_sign != "unknown":
                base.notes.append("hybrid_use_verifier_fallback")
                return _set_hybrid_result(base, sign=base_sign, confidence=max(base_conf, 0.45), distance=base.distance)
            return _set_hybrid_unknown(base, note="hybrid_unknown")

        if policy == HYBRID_BALANCED:
            return self._merge_balanced(base, dfpn, base_sign, dfpn_sign, base_conf, dfpn_conf)

        # Default: conservative.
        return self._merge_conservative(base, dfpn, base_sign, dfpn_sign, base_conf, dfpn_conf)

    def _merge_conservative(
        self,
        base: MateResult,
        dfpn: MateResult,
        base_sign: str,
        dfpn_sign: str,
        base_conf: float,
        dfpn_conf: float,
    ) -> MateResult:
        if base_sign == dfpn_sign and base_sign != "unknown":
            if base_sign == "for_us":
                base.notes.append("hybrid_agree_for_us")
            else:
                base.notes.append("hybrid_agree_for_them")
            distance = base.distance if base_sign == "for_us" else (dfpn.distance or base.distance)
            return _set_hybrid_result(base, sign=base_sign, confidence=max(base_conf, dfpn_conf), distance=distance)

        if base_sign != "unknown" and dfpn_sign != "unknown" and base_sign != dfpn_sign:
            base.notes.append("hybrid_conflict_conservative")
            them_conf = base_conf if base_sign == "for_them" else dfpn_conf
            us_conf = base_conf if base_sign == "for_us" else dfpn_conf
            if them_conf >= max(0.60, us_conf + 0.10):
                return _set_hybrid_result(base, sign="for_them", confidence=them_conf, distance=base.distance or dfpn.distance)
            return _set_hybrid_unknown(base, note="hybrid_hold_unknown")

        known_sign = base_sign if base_sign != "unknown" else dfpn_sign
        known_conf = base_conf if base_sign != "unknown" else dfpn_conf
        known_distance = base.distance if base_sign != "unknown" else dfpn.distance
        if known_sign == "for_them":
            if known_conf >= 0.55:
                base.notes.append("hybrid_conservative_danger")
                return _set_hybrid_result(base, sign="for_them", confidence=known_conf, distance=known_distance)
            return _set_hybrid_unknown(base, note="hybrid_hold_unknown")
        if known_sign == "for_us":
            if known_conf >= 0.72:
                base.notes.append("hybrid_conservative_for_us")
                return _set_hybrid_result(base, sign="for_us", confidence=known_conf, distance=known_distance)
            return _set_hybrid_unknown(base, note="hybrid_hold_unknown")

        return _set_hybrid_unknown(base, note="hybrid_unknown")

    def _merge_balanced(
        self,
        base: MateResult,
        dfpn: MateResult,
        base_sign: str,
        dfpn_sign: str,
        base_conf: float,
        dfpn_conf: float,
    ) -> MateResult:
        score = 0.0
        if base_sign == "for_us":
            score += max(0.20, base_conf)
        elif base_sign == "for_them":
            score -= max(0.20, base_conf)

        if dfpn_sign == "for_us":
            score += max(0.15, dfpn_conf)
        elif dfpn_sign == "for_them":
            score -= max(0.15, dfpn_conf)

        if base_sign != "unknown" and dfpn_sign != "unknown" and base_sign != dfpn_sign:
            base.notes.append("hybrid_conflict_balanced")

        if score >= 0.25:
            distance = base.distance if base_sign == "for_us" else dfpn.distance
            return _set_hybrid_result(base, sign="for_us", confidence=_clamp(0.5 + min(0.45, score / 2.0)), distance=distance)
        if score <= -0.25:
            distance = base.distance if base_sign == "for_them" else dfpn.distance
            return _set_hybrid_result(base, sign="for_them", confidence=_clamp(0.5 + min(0.45, abs(score) / 2.0)), distance=distance)
        return _set_hybrid_unknown(base, note="hybrid_balanced_unknown")

    def _build_position_cmd(self, root_position_cmd: Optional[str], sfen: str, move: str) -> str:
        base = (root_position_cmd or "").strip()
        if not base:
            sfen_raw = (sfen or "").strip()
            if not sfen_raw or sfen_raw == "startpos":
                base = "position startpos"
            else:
                base = f"position sfen {sfen_raw}"
        if not base:
            return ""
        if " moves " in base:
            return f"{base} {move}"
        return f"{base} moves {move}"

    def _build_go_cmd(self, timeout_ms: int, mode: str) -> str:
        bounded_ms = max(20, min(int(timeout_ms), 1200))
        if mode == VERIFY_AGGRESSIVE:
            bounded_ms = max(30, min(1500, bounded_ms + self.verify_aggressive_extra_ms))
        return f"go movetime {bounded_ms}"

    def _ensure_verifier(self, timeout_ms: int, notes: list[str]) -> bool:
        # Keep verifier bootstrap/health timeouts above ultra-short verify budgets.
        startup_timeout_ms = max(int(timeout_ms), 1200)
        health_timeout_ms = max(int(timeout_ms), 800)
        command = self._preferred_command()
        kind = "mate_engine"
        if command is None:
            command = self._fallback_command()
            kind = "backend"
        if command is None:
            notes.append("no_command")
            return False

        command_sig = (command.executable, command.args, kind)
        must_restart = False
        if self._engine is None or self._session is None:
            must_restart = True
        elif not self._engine.alive:
            must_restart = True
            notes.append("engine_dead")
        elif self._last_command_signature != command_sig:
            must_restart = True
            notes.append("command_changed")

        if must_restart:
            if not self._start_preferred(startup_timeout_ms, notes):
                return False

        now = time.time()
        if (now - self._last_ready_ok_ts) >= 1.5:
            if not self._check_ready(health_timeout_ms):
                self._consecutive_health_failures += 1
                notes.append("healthcheck_failed")
                if self._consecutive_health_failures >= 3:
                    if not self._restart_verifier(notes, timeout_ms=max(startup_timeout_ms, 1500)):
                        if self._engine is None or not self._engine.alive:
                            return False
                    elif not self._check_ready(health_timeout_ms):
                        notes.append("healthcheck_failed_after_restart")
                        if self._engine is None or not self._engine.alive:
                            return False
            else:
                self._consecutive_health_failures = 0
        return True

    def _start_preferred(self, timeout_ms: int, notes: list[str]) -> bool:
        preferred = self._preferred_command()
        fallback = self._fallback_command()
        if preferred is not None:
            if self._start_verifier(preferred, "mate_engine", timeout_ms, notes):
                return True
            notes.append("mate_engine_start_failed")
        if fallback is not None:
            if self._start_verifier(fallback, "backend", timeout_ms, notes):
                return True
            notes.append("backend_fallback_start_failed")
        return False

    def _start_verifier(
        self,
        command: EngineCommand,
        engine_kind: str,
        timeout_ms: int,
        notes: list[str],
    ) -> bool:
        self.close()
        try:
            engine = EngineProcess(command=command, cwd=os.getcwd(), encoding="utf-8")
            engine.start()
            self._engine = engine
            self._session = EngineSession(engine, self._config)
            self._engine_kind_running = engine_kind
            if not self._init_usi(timeout_ms):
                notes.append("usi_init_failed")
                self.close()
                return False
            self._last_command_signature = (command.executable, command.args, engine_kind)
            self._initialized = True
            self._consecutive_health_failures = 0
            return True
        except Exception:
            notes.append("start_failed")
            self.close()
            return False

    def _restart_verifier(self, notes: list[str], *, timeout_ms: int = 1500) -> bool:
        notes.append("verifier_restarted")
        return self._start_preferred(max(800, int(timeout_ms)), notes)

    def _preferred_command(self) -> Optional[EngineCommand]:
        p = self.mate_engine_path.strip()
        if not p:
            return None
        return EngineCommand(executable=p, args="")

    def _fallback_command(self) -> Optional[EngineCommand]:
        p = self._fallback_backend_path.strip()
        if not p:
            return None
        return EngineCommand(executable=p, args=self._fallback_backend_args)

    def _init_usi(self, timeout_ms: int) -> bool:
        if self._engine is None:
            return False
        self._engine.drain()
        self._engine.send("usi")
        deadline = time.time() + max(0.3, min(3.0, timeout_ms / 1000.0))

        self._supported_options.clear()
        while time.time() < deadline:
            line = self._engine.recv(self._config.read_timeout)
            if line is None:
                continue
            if line.startswith("option "):
                name = parse_option_name(line)
                if name:
                    self._supported_options.add(name)
                continue
            if line == "usiok":
                break
        else:
            return False

        self._apply_verifier_options()
        return self._check_ready(timeout_ms)

    def _check_ready(self, timeout_ms: int) -> bool:
        if self._engine is None:
            return False
        self._engine.drain()
        self._engine.send("isready")
        deadline = time.time() + max(0.2, min(2.2, timeout_ms / 1000.0))
        while time.time() < deadline:
            line = self._engine.recv(self._config.read_timeout)
            if line is None:
                continue
            if line == "readyok":
                self._last_ready_ok_ts = time.time()
                return True
        return False

    def _apply_verifier_options(self) -> None:
        if self._engine is None:
            return
        # Keep book deterministic in verifier.
        if "BookFile" in self._supported_options:
            self._engine.send("setoption name BookFile value no_book")

        eval_dir = self.mate_engine_eval_dir.strip()
        if eval_dir and "EvalDir" in self._supported_options:
            self._engine.send(f"setoption name EvalDir value {eval_dir}")

        if self._engine_kind_running == "mate_engine":
            profile = _normalize_profile(self.mate_engine_profile)
            if profile == PROFILE_SAFE:
                if "Threads" in self._supported_options:
                    self._engine.send("setoption name Threads value 1")
                if "Hash" in self._supported_options:
                    self._engine.send("setoption name Hash value 64")
            elif profile == PROFILE_FAST_VERIFY:
                if "Threads" in self._supported_options:
                    self._engine.send("setoption name Threads value 2")
                if "Hash" in self._supported_options:
                    self._engine.send("setoption name Hash value 32")

        passthrough = _parse_passthrough(self._backend_option_passthrough)
        for name, value in passthrough.items():
            if name in {"BookFile", "MultiPV"}:
                continue
            if name == "EvalDir" and eval_dir:
                continue
            if name in self._supported_options:
                self._engine.send(f"setoption name {name} value {value}")

        if "MultiPV" in self._supported_options:
            self._engine.send("setoption name MultiPV value 1")


class _NullStdinReader(QueueReadable):
    def get_nowait(self) -> Optional[str]:
        return None


def _effective_sign(result: MateResult) -> str:
    sign = (result.mate_sign or "unknown").strip().lower()
    if sign in {"for_us", "for_them"}:
        return sign
    if result.status == "confirmed":
        return "for_us"
    if result.status == "rejected":
        return "for_them"
    return "unknown"


def _set_hybrid_result(
    base: MateResult,
    *,
    sign: str,
    confidence: float,
    distance: Optional[int] = None,
) -> MateResult:
    base.source = "verify+dfpn"
    base.engine_kind = "hybrid"
    base.mate_sign = sign
    base.confidence = _clamp(max(base.confidence, confidence))
    if sign == "for_us":
        base.status = "confirmed"
        base.found_mate = True
    else:
        base.status = "rejected"
        base.found_mate = False
    if distance is not None:
        base.distance = distance
        base.mate_in = distance
    return base


def _set_hybrid_unknown(base: MateResult, *, note: str) -> MateResult:
    base.source = "verify+dfpn"
    base.engine_kind = "hybrid"
    base.status = "unknown"
    base.mate_sign = "unknown"
    base.found_mate = False
    base.notes.append(note)
    return base


def _clone_result(result: MateResult) -> MateResult:
    return MateResult(
        found_mate=result.found_mate,
        mate_in=result.mate_in,
        distance=result.distance,
        confidence=result.confidence,
        source=result.source,
        status=result.status,
        engine_kind=result.engine_kind,
        mate_sign=result.mate_sign,
        source_detail=result.source_detail,
        raw_summary=result.raw_summary,
        dfpn_dialect_used=result.dfpn_dialect_used,
        dfpn_dialect_candidates=list(result.dfpn_dialect_candidates),
        dfpn_source_detail_normalized=result.dfpn_source_detail_normalized,
        dfpn_pack_source=result.dfpn_pack_source,
        dfpn_pack_version=result.dfpn_pack_version,
        dfpn_pack_load_errors=result.dfpn_pack_load_errors,
        hybrid_learned_adjustment_used=result.hybrid_learned_adjustment_used,
        hybrid_adjustment_delta=result.hybrid_adjustment_delta,
        hybrid_adjustment_source=result.hybrid_adjustment_source,
        notes=list(result.notes),
    )


def _parse_passthrough(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not raw.strip():
        return out
    for chunk in raw.split(";"):
        piece = chunk.strip()
        if not piece or "=" not in piece:
            continue
        k, v = piece.split("=", 1)
        key = k.strip()
        val = v.strip()
        if key:
            out[key] = val
    return out


def _dfpn_placeholder_result(*, status: str, note: str = "") -> MateResult:
    notes: list[str] = []
    if note:
        notes.append(note)
    return MateResult(
        found_mate=False,
        status=status,
        source="dfpn",
        engine_kind="dfpn",
        mate_sign="unknown",
        confidence=0.0,
        source_detail="dfpn:placeholder",
        notes=notes,
    )


def _normalize_verify_mode(mode: str) -> str:
    m = (mode or VERIFY_ONLY).strip().upper()
    if m in {VERIFY_ONLY, VERIFY_TOP, VERIFY_AGGRESSIVE}:
        return m
    return VERIFY_ONLY


def _normalize_hybrid_policy(policy: str) -> str:
    p = (policy or HYBRID_CONSERVATIVE).strip().upper()
    if p in {HYBRID_CONSERVATIVE, HYBRID_BALANCED, HYBRID_MATE_ENGINE_FIRST, HYBRID_DFPN_FIRST}:
        return p
    return HYBRID_CONSERVATIVE


def _normalize_profile(profile: str) -> str:
    p = (profile or PROFILE_AUTO).strip().upper()
    if p in {PROFILE_AUTO, PROFILE_SAFE, PROFILE_FAST_VERIFY}:
        return p
    return PROFILE_AUTO


def _normalize_parser_mode(mode: str) -> str:
    m = (mode or "AUTO").strip().upper()
    if m in {"AUTO", "STRICT", "LOOSE"}:
        return m
    return "AUTO"


def _normalize_dialect(dialect: str) -> str:
    d = (dialect or "AUTO").strip().upper()
    if d in {"AUTO", "GENERIC_EN", "GENERIC_JA", "LEGACY_CLI", "COMPACT"}:
        return d
    return "AUTO"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
