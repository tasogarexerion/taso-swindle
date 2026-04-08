from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .candidate import CandidateMove
    from .context import SwindleContext
    from .scoring import RevWeights


@dataclass
class HybridWeightsModel:
    weights: dict[str, float] = field(default_factory=dict)
    source: str = "none"
    loaded: bool = False
    features_version: str = "unknown"
    label_mode: str = "PSEUDO"


@dataclass
class PonderGateWeightsModel:
    weights: dict[str, float] = field(default_factory=dict)
    source: str = "none"
    loaded: bool = False
    features_version: str = "unknown"
    label_mode: str = "HEURISTIC"
    runtime_label_ratio: float = 0.0
    heuristic_label_ratio: float = 0.0
    avg_label_confidence: float = 0.0


HYBRID_FEATURES_VERSION = "v2"
PONDER_GATE_FEATURES_VERSION = "v1"


class WeightTuner:
    """Weight tuner with optional Phase6 hybrid-confidence adjustment model."""

    def __init__(self) -> None:
        self._hybrid = HybridWeightsModel()
        self._ponder_gate = PonderGateWeightsModel()

    def update(self) -> None:
        return None

    def tune(
        self,
        weights: "RevWeights",
        mode: str,
        context: "SwindleContext",
        candidates: list["CandidateMove"],
    ) -> "RevWeights":
        _ = (mode, context, candidates)
        # REV weights remain pass-through in Phase6.
        return weights

    def load_hybrid_weights(self, path: str) -> bool:
        raw = (path or "").strip()
        if not raw:
            self._hybrid = HybridWeightsModel()
            return False
        if not os.path.exists(raw):
            self._hybrid = HybridWeightsModel(source="missing", loaded=False)
            return False
        try:
            with open(raw, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            self._hybrid = HybridWeightsModel(source="error", loaded=False)
            return False

        if not isinstance(payload, dict):
            self._hybrid = HybridWeightsModel(source="invalid", loaded=False)
            return False

        weights_raw = payload.get("weights")
        if not isinstance(weights_raw, dict):
            self._hybrid = HybridWeightsModel(source="invalid", loaded=False)
            return False

        weights: dict[str, float] = {}
        for key, value in weights_raw.items():
            if not isinstance(key, str):
                continue
            try:
                weights[key] = float(value)
            except Exception:
                continue

        if not weights:
            self._hybrid = HybridWeightsModel(source="invalid", loaded=False)
            return False

        features_version = str(payload.get("features_version", "v1") or "v1")
        label_mode = str(payload.get("label_mode", "PSEUDO") or "PSEUDO").upper()
        self._hybrid = HybridWeightsModel(
            weights=weights,
            source="file",
            loaded=True,
            features_version=features_version,
            label_mode=label_mode,
        )
        return True

    def get_hybrid_adjustment(
        self,
        features: dict[str, Any],
        *,
        cap_pct: float = 15.0,
        require_feature_version_match: bool = False,
        runtime_features_version: str = HYBRID_FEATURES_VERSION,
    ) -> tuple[float, str, bool]:
        if not self._hybrid.loaded:
            return 0.0, self._hybrid.source if self._hybrid.source != "none" else "none", False
        if require_feature_version_match and self._hybrid.features_version != runtime_features_version:
            return 0.0, "version_mismatch", False

        x = _build_hybrid_features(features)
        delta = 0.0
        for key, value in x.items():
            weight = self._hybrid.weights.get(key)
            if weight is None:
                continue
            delta += weight * value

        cap = max(0.0, min(50.0, float(cap_pct))) / 100.0
        if delta > cap:
            delta = cap
        elif delta < -cap:
            delta = -cap
        return delta, self._hybrid.source, True

    def load_ponder_gate_weights(self, path: str) -> bool:
        raw = (path or "").strip()
        if not raw:
            self._ponder_gate = PonderGateWeightsModel()
            return False
        if not os.path.exists(raw):
            self._ponder_gate = PonderGateWeightsModel(source="missing", loaded=False)
            return False
        try:
            with open(raw, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            self._ponder_gate = PonderGateWeightsModel(source="error", loaded=False)
            return False

        if not isinstance(payload, dict):
            self._ponder_gate = PonderGateWeightsModel(source="invalid", loaded=False)
            return False

        weights_raw = payload.get("weights")
        if not isinstance(weights_raw, dict):
            self._ponder_gate = PonderGateWeightsModel(source="invalid", loaded=False)
            return False

        weights: dict[str, float] = {}
        for key, value in weights_raw.items():
            if not isinstance(key, str):
                continue
            try:
                weights[key] = float(value)
            except Exception:
                continue

        if not weights:
            self._ponder_gate = PonderGateWeightsModel(source="invalid", loaded=False)
            return False

        features_version = str(payload.get("features_version", "v1") or "v1")
        label_mode = str(payload.get("label_mode", "HEURISTIC") or "HEURISTIC").upper()
        self._ponder_gate = PonderGateWeightsModel(
            weights=weights,
            source="file",
            loaded=True,
            features_version=features_version,
            label_mode=label_mode,
            runtime_label_ratio=_safe_float(payload.get("runtime_label_ratio")),
            heuristic_label_ratio=_safe_float(payload.get("heuristic_label_ratio")),
            avg_label_confidence=_safe_float(payload.get("avg_label_confidence")),
        )
        return True

    def get_ponder_gate_adjustment(
        self,
        features: dict[str, Any],
        *,
        cap_pct: float = 20.0,
        require_feature_version_match: bool = True,
        runtime_features_version: str = PONDER_GATE_FEATURES_VERSION,
    ) -> tuple[float, str, bool]:
        if not self._ponder_gate.loaded:
            return 0.0, self._ponder_gate.source if self._ponder_gate.source != "none" else "none", False
        if require_feature_version_match and self._ponder_gate.features_version != runtime_features_version:
            return 0.0, "version_mismatch", False

        x = _build_ponder_gate_features(features)
        delta = 0.0
        for key, value in x.items():
            weight = self._ponder_gate.weights.get(key)
            if weight is None:
                continue
            delta += weight * value

        cap = max(0.0, min(100.0, float(cap_pct))) / 100.0
        if delta > cap:
            delta = cap
        elif delta < -cap:
            delta = -cap
        source = self._ponder_gate.source
        if source == "file":
            mode = (self._ponder_gate.label_mode or "HEURISTIC").lower()
            source = f"learned:{mode}"
        return delta, source, True


def _build_hybrid_features(features: dict[str, Any]) -> dict[str, float]:
    verifier_sign = str(features.get("verifier_sign", "unknown"))
    dfpn_sign = str(features.get("dfpn_sign", "unknown"))
    verify_mode = str(features.get("verify_mode", "VERIFY_ONLY")).upper()
    parser_mode = str(features.get("dfpn_parser_mode", "AUTO")).upper()
    source_detail = str(features.get("dfpn_source_detail", ""))

    agree = 1.0 if verifier_sign in {"for_us", "for_them"} and verifier_sign == dfpn_sign else 0.0
    conflict = 1.0 if verifier_sign in {"for_us", "for_them"} and dfpn_sign in {"for_us", "for_them"} and verifier_sign != dfpn_sign else 0.0
    distance_available = 1.0 if features.get("dfpn_distance") is not None else 0.0
    strict_hit = 1.0 if "strict" in source_detail else 0.0
    loose_hit = 1.0 if "loose" in source_detail else 0.0

    x: dict[str, float] = {
        "bias": 1.0,
        "agree": agree,
        "conflict": conflict,
        "verifier_for_us": 1.0 if verifier_sign == "for_us" else 0.0,
        "verifier_for_them": 1.0 if verifier_sign == "for_them" else 0.0,
        "dfpn_for_us": 1.0 if dfpn_sign == "for_us" else 0.0,
        "dfpn_for_them": 1.0 if dfpn_sign == "for_them" else 0.0,
        "verifier_conf": _safe_float(features.get("verifier_confidence")),
        "dfpn_conf": _safe_float(features.get("dfpn_confidence")),
        "distance_available": distance_available,
        "strict_hit": strict_hit,
        "loose_hit": loose_hit,
        "actual_in_topk": 1.0 if bool(features.get("actual_move_in_reply_topk", False)) else 0.0,
        "actual_rank_inv": _rank_inverse(features.get("actual_move_rank_in_reply_topk")),
        "outcome_win": 1.0 if str(features.get("outcome_tag", "")).lower() in {"win", "swing_success"} else 0.0,
        "outcome_loss": 1.0 if str(features.get("outcome_tag", "")).lower() in {"loss", "swing_fail"} else 0.0,
        "outcome_draw": 1.0 if str(features.get("outcome_tag", "")).lower() == "draw" else 0.0,
        "mode_top": 1.0 if verify_mode == "TOP_CANDIDATES" else 0.0,
        "mode_aggressive": 1.0 if verify_mode == "AGGRESSIVE" else 0.0,
        "parser_strict": 1.0 if parser_mode == "STRICT" else 0.0,
        "parser_loose": 1.0 if parser_mode == "LOOSE" else 0.0,
        "emergency": 1.0 if bool(features.get("emergency_fast_mode", False)) else 0.0,
    }
    return x


def _safe_float(value: Any) -> float:
    try:
        v = float(value)
    except Exception:
        return 0.0
    if v != v:  # NaN guard
        return 0.0
    if v == float("inf") or v == float("-inf"):
        return 0.0
    return v


def _build_ponder_gate_features(features: dict[str, Any]) -> dict[str, float]:
    reply_coverage = _clamp01(_safe_float(features.get("reply_coverage")))
    candidate_count = _safe_float(features.get("candidate_count"))
    top_gap12 = max(0.0, _safe_float(features.get("top_gap12")))
    had_mate_signal = bool(features.get("had_mate_signal", False))
    elapsed_ms = max(0.0, _safe_float(features.get("elapsed_ms")))
    cache_age_ms = max(0.0, _safe_float(features.get("cache_age_ms")))
    max_age_ms = max(1.0, _safe_float(features.get("max_age_ms")) or 3000.0)
    verify_done = bool(features.get("verify_done_for_mate_cache", False))
    reuse_changed = bool(features.get("reuse_then_bestmove_changed", False))

    x: dict[str, float] = {
        "bias": 1.0,
        "reply_coverage": reply_coverage,
        "candidate_count": _clamp01(candidate_count / 8.0),
        "top_gap12": _clamp01(top_gap12 / 1000.0),
        "had_mate_signal": 1.0 if had_mate_signal else 0.0,
        "elapsed_ms": _clamp01(elapsed_ms / 300.0),
        "cache_age_ms": _clamp01(cache_age_ms / max_age_ms),
        "verify_done_for_mate_cache": 1.0 if verify_done else 0.0,
        "reuse_then_bestmove_changed": 1.0 if reuse_changed else 0.0,
    }
    return x


def _rank_inverse(value: Any) -> float:
    try:
        rank = int(value)
    except Exception:
        return 0.0
    if rank <= 0:
        return 0.0
    return 1.0 / float(rank)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
