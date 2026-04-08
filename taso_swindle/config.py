from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, Optional


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_bool(name: str, default: str) -> bool:
    return _parse_bool(os.environ.get(name, default))


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class USIOptionSpec:
    name: str
    option_type: str
    default: str
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    vars: tuple[str, ...] = ()

    def to_usi_line(self) -> str:
        parts = ["option", "name", self.name, "type", self.option_type]
        if self.option_type == "spin":
            parts.extend(["default", self.default])
            if self.min_value is not None:
                parts.extend(["min", str(self.min_value)])
            if self.max_value is not None:
                parts.extend(["max", str(self.max_value)])
            return " ".join(parts)

        if self.option_type == "combo":
            parts.extend(["default", self.default])
            for var in self.vars:
                parts.extend(["var", var])
            return " ".join(parts)

        if self.option_type in {"string", "check"}:
            parts.extend(["default", self.default])
            return " ".join(parts)

        return " ".join(parts)


@dataclass(frozen=True)
class OptionApplyResult:
    handled: bool
    restart_required: bool = False


@dataclass
class SwindleConfig:
    engine_name: str = "TASO-SWINDLE"
    engine_author: str = "Codex"

    backend_engine_path: str = os.environ.get("TASO_SWINDLE_BACKEND_ENGINE", "./YaneuraOu")
    backend_engine_args: str = os.environ.get("TASO_SWINDLE_BACKEND_ARGS", "")
    backend_engine_option_passthrough: str = os.environ.get("TASO_SWINDLE_BACKEND_OPTION_PASSTHROUGH", "")
    mate_engine_path: str = ""
    use_mate_engine: bool = False

    swindle_enable: bool = _env_bool("TASO_SWINDLE_SWINDLE_ENABLE", "true")
    swindle_mode: str = os.environ.get("TASO_SWINDLE_SWINDLE_MODE", "HYBRID").strip().upper() or "HYBRID"
    swindle_level: int = 3

    swindle_eval_threshold_cp: int = -700
    swindle_force_at_mate_loss: bool = True
    swindle_disable_vs_engine: bool = False

    swindle_multipv: int = 12
    swindle_min_depth: int = 12
    swindle_max_candidates: int = 6
    swindle_reply_multipv: int = 4
    swindle_reply_depth: int = 10
    swindle_reply_nodes: int = 0
    swindle_reply_topk: int = 4
    swindle_use_adaptive_reply_budget: bool = True

    swindle_eval_drop_cap_cp: int = 500
    swindle_dynamic_drop_cap: bool = True
    swindle_drop_cap_at_losing_cp: int = 800
    swindle_drop_cap_at_lost_cp: int = 1200

    weight_mate_urgency: int = 1000
    weight_threat: int = 220
    weight_onlymove: int = 260
    weight_reply_entropy: int = 120
    weight_human_trap: int = 180
    weight_self_risk: int = 260
    weight_survival: int = 140

    swindle_mate_priority: bool = True
    swindle_use_mate_engine_verification: bool = False
    swindle_mate_verify_time_ms: int = 300
    swindle_verify_mode: str = "VERIFY_ONLY"
    swindle_mate_engine_path: str = ""
    swindle_mate_engine_eval_dir: str = ""
    swindle_use_dfpn: bool = False
    swindle_dfpn_path: str = ""
    swindle_dfpn_time_ms: int = 120
    swindle_dfpn_parser_mode: str = "AUTO"
    swindle_dfpn_dialect: str = "AUTO"
    swindle_dfpn_dialect_pack_path: str = ""
    swindle_verify_hybrid_policy: str = "CONSERVATIVE"
    swindle_mate_engine_profile: str = "AUTO"
    swindle_hybrid_weights_path: str = os.environ.get(
        "TASO_SWINDLE_SWINDLE_HYBRID_WEIGHTS_PATH",
        "./logs/taso-swindle/hybrid_weights.json",
    )
    swindle_use_hybrid_learned_adjustment: bool = _env_bool(
        "TASO_SWINDLE_SWINDLE_USE_HYBRID_LEARNED_ADJUSTMENT",
        "false",
    )
    swindle_hybrid_adjustment_cap_pct: int = 15
    swindle_hybrid_label_mode: str = "PSEUDO"
    swindle_hybrid_require_feature_version_match: bool = True
    swindle_verify_max_candidates: int = 4
    swindle_verify_aggressive_extra_ms: int = 120
    swindle_ponder_mate_verify: bool = False
    swindle_ponder_enable: bool = False
    swindle_ponder_verify: bool = False
    swindle_ponder_dfpn: bool = False
    swindle_ponder_max_ms: int = 500
    swindle_ponder_reuse_min_score: int = 55
    swindle_ponder_cache_max_age_ms: int = 3000
    swindle_ponder_require_verify_for_mate_cache: bool = True
    swindle_ponder_gate_weights_path: str = ""
    swindle_use_ponder_gate_learned_adjustment: bool = _env_bool(
        "TASO_SWINDLE_SWINDLE_USE_PONDER_GATE_LEARNED_ADJUSTMENT",
        "false",
    )
    swindle_ponder_reuse_learned_adjustment_cap_pct: int = 20
    swindle_pseudo_hisshi_detect: bool = True
    swindle_pseudo_hisshi_window_ply: int = 6
    swindle_respect_byoyomi: bool = True
    swindle_reserve_time_ms: int = 200
    swindle_emergency_fast_mode_ms: int = 1500

    swindle_verbose_info: bool = _env_bool("TASO_SWINDLE_SWINDLE_VERBOSE_INFO", "true")
    swindle_show_ranking: bool = True
    swindle_log_enable: bool = _env_bool("TASO_SWINDLE_SWINDLE_LOG_ENABLE", "true")
    swindle_log_path: str = "./logs/taso-swindle/"
    swindle_log_format: str = "JSONL"
    swindle_emit_info_string_level: int = _env_int("TASO_SWINDLE_SWINDLE_EMIT_INFO_STRING_LEVEL", 2)

    swindle_deterministic_seed: int = 0
    swindle_ablation_mode: str = "NONE"
    swindle_dry_run: bool = False

    # Reference: nnue_proxy.py:90+ timeout/safety constants style
    read_timeout: float = float(os.environ.get("TASO_SWINDLE_READ_TIMEOUT", "0.1"))
    go_hard_sec: float = float(os.environ.get("TASO_SWINDLE_GO_HARD_SEC", "60.0"))
    go_hard_sec_infinite: float = float(os.environ.get("TASO_SWINDLE_GO_HARD_SEC_INFINITE", "0.0"))
    go_stop_grace_sec: float = float(os.environ.get("TASO_SWINDLE_GO_STOP_GRACE_SEC", "3.0"))
    isready_timeout_sec: float = float(os.environ.get("TASO_SWINDLE_ISREADY_TIMEOUT", "10.0"))
    usi_init_timeout_sec: float = float(os.environ.get("TASO_SWINDLE_USI_INIT_TIMEOUT", "10.0"))

    encoding: str = "utf-8"

    _specs_by_name: Dict[str, USIOptionSpec] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._specs_by_name = {spec.name: spec for spec in usi_option_specs()}

    def iter_usi_option_lines(self) -> Iterable[str]:
        for spec in usi_option_specs():
            yield spec.to_usi_line()

    def apply_usi_option(self, name: str, value: str) -> OptionApplyResult:
        converters: Dict[str, Callable[[str], None]] = {
            "BackendEnginePath": lambda v: setattr(self, "backend_engine_path", v),
            "BackendEngineArgs": lambda v: setattr(self, "backend_engine_args", v),
            "BackendEngineOptionPassthrough": lambda v: setattr(self, "backend_engine_option_passthrough", v),
            "MateEnginePath": lambda v: setattr(self, "mate_engine_path", v),
            "UseMateEngine": lambda v: setattr(self, "use_mate_engine", _parse_bool(v)),
            "SwindleEnable": lambda v: setattr(self, "swindle_enable", _parse_bool(v)),
            "SwindleMode": lambda v: setattr(self, "swindle_mode", v.strip().upper() or "HYBRID"),
            "SwindleLevel": lambda v: setattr(self, "swindle_level", _clamp_int(int(v or "3"), 1, 5)),
            "SwindleEvalThresholdCp": lambda v: setattr(self, "swindle_eval_threshold_cp", _clamp_int(int(v or "-700"), -5000, 0)),
            "SwindleForceAtMateLoss": lambda v: setattr(self, "swindle_force_at_mate_loss", _parse_bool(v)),
            "SwindleDisableVsEngine": lambda v: setattr(self, "swindle_disable_vs_engine", _parse_bool(v)),
            "SwindleMultiPV": lambda v: setattr(self, "swindle_multipv", _clamp_int(int(v or "12"), 2, 32)),
            "SwindleMinDepth": lambda v: setattr(self, "swindle_min_depth", _clamp_int(int(v or "12"), 1, 64)),
            "SwindleMaxCandidates": lambda v: setattr(self, "swindle_max_candidates", _clamp_int(int(v or "6"), 2, 16)),
            "SwindleReplyMultiPV": lambda v: setattr(self, "swindle_reply_multipv", _clamp_int(int(v or "4"), 2, 16)),
            "SwindleReplyDepth": lambda v: setattr(self, "swindle_reply_depth", _clamp_int(int(v or "10"), 4, 32)),
            "SwindleReplyNodes": lambda v: setattr(self, "swindle_reply_nodes", _clamp_int(int(v or "0"), 0, 1_000_000_000)),
            "SwindleReplyTopK": lambda v: setattr(self, "swindle_reply_topk", _clamp_int(int(v or "4"), 1, 8)),
            "SwindleUseAdaptiveReplyBudget": lambda v: setattr(self, "swindle_use_adaptive_reply_budget", _parse_bool(v)),
            "SwindleEvalDropCapCp": lambda v: setattr(self, "swindle_eval_drop_cap_cp", _clamp_int(int(v or "500"), 0, 5000)),
            "SwindleDynamicDropCap": lambda v: setattr(self, "swindle_dynamic_drop_cap", _parse_bool(v)),
            "SwindleDropCapAtLosingCp": lambda v: setattr(self, "swindle_drop_cap_at_losing_cp", _clamp_int(int(v or "800"), 0, 5000)),
            "SwindleDropCapAtLostCp": lambda v: setattr(self, "swindle_drop_cap_at_lost_cp", _clamp_int(int(v or "1200"), 0, 5000)),
            "WeightMateUrgency": lambda v: setattr(self, "weight_mate_urgency", _clamp_int(int(v or "1000"), 0, 1000)),
            "WeightThreat": lambda v: setattr(self, "weight_threat", _clamp_int(int(v or "220"), 0, 1000)),
            "WeightOnlyMove": lambda v: setattr(self, "weight_onlymove", _clamp_int(int(v or "260"), 0, 1000)),
            "WeightReplyEntropy": lambda v: setattr(self, "weight_reply_entropy", _clamp_int(int(v or "120"), 0, 1000)),
            "WeightHumanTrap": lambda v: setattr(self, "weight_human_trap", _clamp_int(int(v or "180"), 0, 1000)),
            "WeightSelfRisk": lambda v: setattr(self, "weight_self_risk", _clamp_int(int(v or "260"), 0, 1000)),
            "WeightSurvival": lambda v: setattr(self, "weight_survival", _clamp_int(int(v or "140"), 0, 1000)),
            "SwindleMatePriority": lambda v: setattr(self, "swindle_mate_priority", _parse_bool(v)),
            "SwindleUseMateEngineVerification": lambda v: setattr(self, "swindle_use_mate_engine_verification", _parse_bool(v)),
            "SwindleMateVerifyTimeMs": lambda v: setattr(self, "swindle_mate_verify_time_ms", _clamp_int(int(v or "300"), 1, 5000)),
            "SwindleVerifyMode": lambda v: setattr(self, "swindle_verify_mode", (v.strip().upper() or "VERIFY_ONLY")),
            "SwindleMateEnginePath": lambda v: setattr(self, "swindle_mate_engine_path", v),
            "SwindleMateEngineEvalDir": lambda v: setattr(self, "swindle_mate_engine_eval_dir", v),
            "SwindleUseDfPn": lambda v: setattr(self, "swindle_use_dfpn", _parse_bool(v)),
            "SwindleDfPnPath": lambda v: setattr(self, "swindle_dfpn_path", v),
            "SwindleDfPnTimeMs": lambda v: setattr(self, "swindle_dfpn_time_ms", _clamp_int(int(v or "120"), 1, 10000)),
            "SwindleDfPnParserMode": lambda v: setattr(self, "swindle_dfpn_parser_mode", (v.strip().upper() or "AUTO")),
            "SwindleDfPnDialect": lambda v: setattr(self, "swindle_dfpn_dialect", (v.strip().upper() or "AUTO")),
            "SwindleDfPnDialectPackPath": lambda v: setattr(self, "swindle_dfpn_dialect_pack_path", v),
            "SwindleVerifyHybridPolicy": lambda v: setattr(self, "swindle_verify_hybrid_policy", (v.strip().upper() or "CONSERVATIVE")),
            "SwindleMateEngineProfile": lambda v: setattr(self, "swindle_mate_engine_profile", (v.strip().upper() or "AUTO")),
            "SwindleHybridWeightsPath": lambda v: setattr(self, "swindle_hybrid_weights_path", v),
            "SwindleUseHybridLearnedAdjustment": lambda v: setattr(self, "swindle_use_hybrid_learned_adjustment", _parse_bool(v)),
            "SwindleHybridAdjustmentCapPct": lambda v: setattr(self, "swindle_hybrid_adjustment_cap_pct", _clamp_int(int(v or "15"), 0, 50)),
            "SwindleHybridLabelMode": lambda v: setattr(self, "swindle_hybrid_label_mode", (v.strip().upper() or "PSEUDO")),
            "SwindleHybridRequireFeatureVersionMatch": lambda v: setattr(self, "swindle_hybrid_require_feature_version_match", _parse_bool(v)),
            "SwindleVerifyMaxCandidates": lambda v: setattr(self, "swindle_verify_max_candidates", _clamp_int(int(v or "4"), 1, 16)),
            "SwindleVerifyAggressiveExtraMs": lambda v: setattr(self, "swindle_verify_aggressive_extra_ms", _clamp_int(int(v or "120"), 0, 10000)),
            "SwindlePonderEnable": lambda v: setattr(self, "swindle_ponder_enable", _parse_bool(v)),
            "SwindlePonderVerify": lambda v: setattr(self, "swindle_ponder_verify", _parse_bool(v)),
            "SwindlePonderDfPn": lambda v: setattr(self, "swindle_ponder_dfpn", _parse_bool(v)),
            "SwindlePonderMaxMs": lambda v: setattr(self, "swindle_ponder_max_ms", _clamp_int(int(v or "500"), 0, 10000)),
            "SwindlePonderReuseMinScore": lambda v: setattr(self, "swindle_ponder_reuse_min_score", _clamp_int(int(v or "55"), 0, 100)),
            "SwindlePonderCacheMaxAgeMs": lambda v: setattr(self, "swindle_ponder_cache_max_age_ms", _clamp_int(int(v or "3000"), 0, 60000)),
            "SwindlePonderRequireVerifyForMateCache": lambda v: setattr(self, "swindle_ponder_require_verify_for_mate_cache", _parse_bool(v)),
            "SwindlePonderGateWeightsPath": lambda v: setattr(self, "swindle_ponder_gate_weights_path", v),
            "SwindleUsePonderGateLearnedAdjustment": lambda v: setattr(self, "swindle_use_ponder_gate_learned_adjustment", _parse_bool(v)),
            "SwindlePonderReuseLearnedAdjustmentCapPct": lambda v: setattr(self, "swindle_ponder_reuse_learned_adjustment_cap_pct", _clamp_int(int(v or "20"), 0, 100)),
            "SwindlePonderMateVerify": lambda v: (
                setattr(self, "swindle_ponder_mate_verify", _parse_bool(v)),
                setattr(self, "swindle_ponder_verify", _parse_bool(v)),
            ),
            "SwindlePseudoHisshiDetect": lambda v: setattr(self, "swindle_pseudo_hisshi_detect", _parse_bool(v)),
            "SwindlePseudoHisshiWindowPly": lambda v: setattr(self, "swindle_pseudo_hisshi_window_ply", _clamp_int(int(v or "6"), 1, 20)),
            "SwindleRespectByoyomi": lambda v: setattr(self, "swindle_respect_byoyomi", _parse_bool(v)),
            "SwindleReserveTimeMs": lambda v: setattr(self, "swindle_reserve_time_ms", _clamp_int(int(v or "200"), 0, 10000)),
            "SwindleEmergencyFastModeMs": lambda v: setattr(self, "swindle_emergency_fast_mode_ms", _clamp_int(int(v or "1500"), 0, 10000)),
            "SwindleVerboseInfo": lambda v: setattr(self, "swindle_verbose_info", _parse_bool(v)),
            "SwindleShowRanking": lambda v: setattr(self, "swindle_show_ranking", _parse_bool(v)),
            "SwindleLogEnable": lambda v: setattr(self, "swindle_log_enable", _parse_bool(v)),
            "SwindleLogPath": lambda v: setattr(self, "swindle_log_path", v),
            "SwindleLogFormat": lambda v: setattr(self, "swindle_log_format", (v.strip().upper() or "JSONL")),
            "SwindleEmitInfoStringLevel": lambda v: setattr(self, "swindle_emit_info_string_level", _clamp_int(int(v or "2"), 0, 3)),
            "SwindleDeterministicSeed": lambda v: setattr(self, "swindle_deterministic_seed", _clamp_int(int(v or "0"), 0, 2_147_483_647)),
            "SwindleAblationMode": lambda v: setattr(self, "swindle_ablation_mode", v.strip().upper() or "NONE"),
            "SwindleDryRun": lambda v: setattr(self, "swindle_dry_run", _parse_bool(v)),
        }

        if name not in converters:
            return OptionApplyResult(handled=False)

        try:
            converters[name](value)
        except Exception:
            return OptionApplyResult(handled=True)

        restart_required = name in {"BackendEnginePath", "BackendEngineArgs"}
        return OptionApplyResult(handled=True, restart_required=restart_required)

    def dynamic_drop_cap_cp(self, root_eval_cp: Optional[int], root_mate: Optional[int]) -> int:
        if not self.swindle_dynamic_drop_cap:
            return self.swindle_eval_drop_cap_cp

        if root_mate is not None and root_mate < 0:
            return self.swindle_drop_cap_at_lost_cp

        if root_eval_cp is None:
            return self.swindle_eval_drop_cap_cp

        if root_eval_cp <= self.swindle_eval_threshold_cp - 600:
            return self.swindle_drop_cap_at_lost_cp

        if root_eval_cp <= self.swindle_eval_threshold_cp:
            return self.swindle_drop_cap_at_losing_cp

        return self.swindle_eval_drop_cap_cp

    def parse_backend_option_passthrough(self) -> Dict[str, str]:
        result: Dict[str, str] = {}
        raw = self.backend_engine_option_passthrough.strip()
        if not raw:
            return result

        for chunk in raw.split(";"):
            piece = chunk.strip()
            if not piece:
                continue
            if "=" not in piece:
                continue
            name, value = piece.split("=", 1)
            key = name.strip()
            val = value.strip()
            if key:
                result[key] = val
        return result


def usi_option_specs() -> list[USIOptionSpec]:
    return [
        USIOptionSpec("BackendEnginePath", "string", "./YaneuraOu"),
        USIOptionSpec("BackendEngineArgs", "string", ""),
        USIOptionSpec("BackendEngineOptionPassthrough", "string", ""),
        USIOptionSpec("MateEnginePath", "string", ""),
        USIOptionSpec("UseMateEngine", "check", "false"),
        USIOptionSpec("SwindleEnable", "check", "true"),
        USIOptionSpec("SwindleMode", "combo", "HYBRID", vars=("AUTO", "TACTICAL", "MURKY", "HYBRID")),
        USIOptionSpec("SwindleLevel", "spin", "3", min_value=1, max_value=5),
        USIOptionSpec("SwindleEvalThresholdCp", "spin", "-700", min_value=-5000, max_value=0),
        USIOptionSpec("SwindleForceAtMateLoss", "check", "true"),
        USIOptionSpec("SwindleDisableVsEngine", "check", "false"),
        USIOptionSpec("SwindleMultiPV", "spin", "12", min_value=2, max_value=32),
        USIOptionSpec("SwindleMinDepth", "spin", "12", min_value=1, max_value=64),
        USIOptionSpec("SwindleMaxCandidates", "spin", "6", min_value=2, max_value=16),
        USIOptionSpec("SwindleReplyMultiPV", "spin", "4", min_value=2, max_value=16),
        USIOptionSpec("SwindleReplyDepth", "spin", "10", min_value=4, max_value=32),
        USIOptionSpec("SwindleReplyNodes", "spin", "0", min_value=0, max_value=1_000_000_000),
        USIOptionSpec("SwindleReplyTopK", "spin", "4", min_value=1, max_value=8),
        USIOptionSpec("SwindleUseAdaptiveReplyBudget", "check", "true"),
        USIOptionSpec("SwindleEvalDropCapCp", "spin", "500", min_value=0, max_value=5000),
        USIOptionSpec("SwindleDynamicDropCap", "check", "true"),
        USIOptionSpec("SwindleDropCapAtLosingCp", "spin", "800", min_value=0, max_value=5000),
        USIOptionSpec("SwindleDropCapAtLostCp", "spin", "1200", min_value=0, max_value=5000),
        USIOptionSpec("WeightMateUrgency", "spin", "1000", min_value=0, max_value=1000),
        USIOptionSpec("WeightThreat", "spin", "220", min_value=0, max_value=1000),
        USIOptionSpec("WeightOnlyMove", "spin", "260", min_value=0, max_value=1000),
        USIOptionSpec("WeightReplyEntropy", "spin", "120", min_value=0, max_value=1000),
        USIOptionSpec("WeightHumanTrap", "spin", "180", min_value=0, max_value=1000),
        USIOptionSpec("WeightSelfRisk", "spin", "260", min_value=0, max_value=1000),
        USIOptionSpec("WeightSurvival", "spin", "140", min_value=0, max_value=1000),
        USIOptionSpec("SwindleMatePriority", "check", "true"),
        USIOptionSpec("SwindleUseMateEngineVerification", "check", "false"),
        USIOptionSpec("SwindleMateVerifyTimeMs", "spin", "300", min_value=1, max_value=5000),
        USIOptionSpec("SwindleVerifyMode", "combo", "VERIFY_ONLY", vars=("VERIFY_ONLY", "TOP_CANDIDATES", "AGGRESSIVE")),
        USIOptionSpec("SwindleMateEnginePath", "string", ""),
        USIOptionSpec("SwindleMateEngineEvalDir", "string", ""),
        USIOptionSpec("SwindleUseDfPn", "check", "false"),
        USIOptionSpec("SwindleDfPnPath", "string", ""),
        USIOptionSpec("SwindleDfPnTimeMs", "spin", "120", min_value=1, max_value=10000),
        USIOptionSpec("SwindleDfPnParserMode", "combo", "AUTO", vars=("AUTO", "STRICT", "LOOSE")),
        USIOptionSpec("SwindleDfPnDialect", "combo", "AUTO", vars=("AUTO", "GENERIC_EN", "GENERIC_JA", "LEGACY_CLI", "COMPACT")),
        USIOptionSpec("SwindleDfPnDialectPackPath", "string", ""),
        USIOptionSpec("SwindleVerifyHybridPolicy", "combo", "CONSERVATIVE", vars=("CONSERVATIVE", "BALANCED", "MATE_ENGINE_FIRST", "DFPN_FIRST")),
        USIOptionSpec("SwindleMateEngineProfile", "combo", "AUTO", vars=("AUTO", "SAFE", "FAST_VERIFY")),
        USIOptionSpec("SwindleHybridWeightsPath", "string", "./logs/taso-swindle/hybrid_weights.json"),
        USIOptionSpec("SwindleUseHybridLearnedAdjustment", "check", "false"),
        USIOptionSpec("SwindleHybridAdjustmentCapPct", "spin", "15", min_value=0, max_value=50),
        USIOptionSpec("SwindleHybridLabelMode", "combo", "PSEUDO", vars=("PSEUDO", "SUPERVISED", "MIXED")),
        USIOptionSpec("SwindleHybridRequireFeatureVersionMatch", "check", "true"),
        USIOptionSpec("SwindleVerifyMaxCandidates", "spin", "4", min_value=1, max_value=16),
        USIOptionSpec("SwindleVerifyAggressiveExtraMs", "spin", "120", min_value=0, max_value=10000),
        USIOptionSpec("SwindlePonderEnable", "check", "false"),
        USIOptionSpec("SwindlePonderVerify", "check", "false"),
        USIOptionSpec("SwindlePonderDfPn", "check", "false"),
        USIOptionSpec("SwindlePonderMaxMs", "spin", "500", min_value=0, max_value=10000),
        USIOptionSpec("SwindlePonderReuseMinScore", "spin", "55", min_value=0, max_value=100),
        USIOptionSpec("SwindlePonderCacheMaxAgeMs", "spin", "3000", min_value=0, max_value=60000),
        USIOptionSpec("SwindlePonderRequireVerifyForMateCache", "check", "true"),
        USIOptionSpec("SwindlePonderGateWeightsPath", "string", ""),
        USIOptionSpec("SwindleUsePonderGateLearnedAdjustment", "check", "false"),
        USIOptionSpec("SwindlePonderReuseLearnedAdjustmentCapPct", "spin", "20", min_value=0, max_value=100),
        USIOptionSpec("SwindlePonderMateVerify", "check", "false"),
        USIOptionSpec("SwindlePseudoHisshiDetect", "check", "true"),
        USIOptionSpec("SwindlePseudoHisshiWindowPly", "spin", "6", min_value=1, max_value=20),
        USIOptionSpec("SwindleRespectByoyomi", "check", "true"),
        USIOptionSpec("SwindleReserveTimeMs", "spin", "200", min_value=0, max_value=10000),
        USIOptionSpec("SwindleEmergencyFastModeMs", "spin", "1500", min_value=0, max_value=10000),
        USIOptionSpec("SwindleVerboseInfo", "check", "true"),
        USIOptionSpec("SwindleShowRanking", "check", "true"),
        USIOptionSpec("SwindleLogEnable", "check", "true"),
        USIOptionSpec("SwindleLogPath", "string", "./logs/taso-swindle/"),
        USIOptionSpec("SwindleLogFormat", "combo", "JSONL", vars=("JSONL", "CSV")),
        USIOptionSpec("SwindleEmitInfoStringLevel", "spin", "2", min_value=0, max_value=3),
        USIOptionSpec("SwindleDeterministicSeed", "spin", "0", min_value=0, max_value=2147483647),
        USIOptionSpec("SwindleAblationMode", "combo", "NONE", vars=("NONE", "NO_MATE", "NO_ONLYMOVE", "NO_TRAP", "NO_ENTROPY")),
        USIOptionSpec("SwindleDryRun", "check", "false"),
    ]
