#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.swindle.weight_tuner import PONDER_GATE_FEATURES_VERSION


FEATURE_KEYS = [
    "bias",
    "reply_coverage",
    "candidate_count",
    "top_gap12",
    "had_mate_signal",
    "elapsed_ms",
    "cache_age_ms",
    "verify_done_for_mate_cache",
    "reuse_then_bestmove_changed",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v in {float("inf"), float("-inf")}:
        return default
    return v


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _feature_row(rec: dict[str, Any]) -> dict[str, float]:
    candidates = rec.get("candidates")
    cand_list = candidates if isinstance(candidates, list) else []

    candidate_count = float(len(cand_list))
    coverages: list[float] = []
    top_gap12 = 0.0
    had_mate_signal = False
    for idx, cand in enumerate(cand_list):
        if not isinstance(cand, dict):
            continue
        if cand.get("mate") is not None:
            had_mate_signal = True
        if idx == 0:
            top_gap12 = max(0.0, _safe_float(cand.get("gap12")))
        reply_topk = cand.get("reply_topk")
        if isinstance(reply_topk, list):
            coverages.append(_clamp01(float(len(reply_topk)) / 4.0))

    reply_coverage = sum(coverages) / float(len(coverages)) if coverages else 0.0
    elapsed_ms = max(0.0, _safe_float(rec.get("ponder_used_budget_ms")))
    cache_age_ms = max(0.0, _safe_float(rec.get("ponder_cache_age_ms")))
    max_age_ms = max(1.0, _safe_float(rec.get("swindle_ponder_cache_max_age_ms")) or 3000.0)
    verify_status = str(rec.get("verify_status_summary", "not_used"))
    verify_done = verify_status not in {"not_used", "skipped"}
    changed = bool(rec.get("reuse_then_bestmove_changed", False))

    return {
        "bias": 1.0,
        "reply_coverage": _clamp01(reply_coverage),
        "candidate_count": _clamp01(candidate_count / 8.0),
        "top_gap12": _clamp01(top_gap12 / 1000.0),
        "had_mate_signal": 1.0 if had_mate_signal else 0.0,
        "elapsed_ms": _clamp01(elapsed_ms / 300.0),
        "cache_age_ms": _clamp01(cache_age_ms / max_age_ms),
        "verify_done_for_mate_cache": 1.0 if verify_done else 0.0,
        "reuse_then_bestmove_changed": 1.0 if changed else 0.0,
    }


def _label_heuristic(rec: dict[str, Any]) -> Optional[float]:
    cache_hit = bool(rec.get("ponder_cache_hit", False))
    cache_used = bool(rec.get("ponder_cache_used", False))
    gate_reason = str(rec.get("ponder_cache_gate_reason") or "")
    selected = str(rec.get("selected_reason") or "")
    restarts = int(_safe_float(rec.get("backend_restart_count")))
    status = str(rec.get("ponder_status_summary", "not_used"))

    if cache_hit and (not cache_used) and gate_reason in {"quality_gate", "mate_without_verify", "stale", "position_miss"}:
        return 0.0
    if cache_used:
        if selected in {"ponder_fallback", "fallback_backend", "fallback_resign"} or restarts > 0:
            return 0.0
        return 1.0
    if status in {"fallback", "timeout", "error"}:
        return 0.0
    return None


def _label_supervised(rec: dict[str, Any]) -> Optional[float]:
    explicit = rec.get("reuse_good")
    if isinstance(explicit, bool):
        return 1.0 if explicit else 0.0

    changed = rec.get("reuse_then_bestmove_changed")
    if isinstance(changed, bool):
        return 0.0 if changed else 1.0

    tag = str(rec.get("outcome_tag", "")).lower()
    cache_used = bool(rec.get("ponder_cache_used", False))
    if not cache_used:
        return None
    if tag in {"win", "swing_success"}:
        return 1.0
    if tag in {"loss", "swing_fail"}:
        return 0.0
    return None


def _resolve_label(rec: dict[str, Any], label_mode: str) -> tuple[Optional[float], str, float]:
    lm = (label_mode or "heuristic").strip().lower()
    runtime_target = rec.get("ponder_reuse_target")
    runtime_source = str(rec.get("ponder_label_source", "runtime_observed") or "runtime_observed").lower()
    runtime_conf = _clamp01(_safe_float(rec.get("ponder_label_confidence"), 0.9))
    runtime: Optional[float] = None
    if runtime_target is not None:
        runtime = _clamp01(_safe_float(runtime_target))
    if runtime is None:
        changed = rec.get("reuse_then_bestmove_changed")
        if isinstance(changed, bool) and bool(rec.get("ponder_cache_used", False)):
            runtime = 0.0 if changed else 1.0
            runtime_source = "runtime_observed"
            if runtime_conf <= 0.0:
                runtime_conf = 0.8

    sup = _label_supervised(rec)
    heu = _label_heuristic(rec)
    heur_conf = 0.35 if heu is not None else 0.0

    if lm == "supervised":
        if runtime is not None:
            return runtime, (runtime_source or "runtime_observed"), runtime_conf
        return sup, "supervised", 0.7 if sup is not None else 0.0
    if lm == "mixed":
        if runtime is not None:
            return runtime, (runtime_source or "runtime_observed"), runtime_conf
        if sup is not None:
            return sup, "supervised", 0.6
        return heu, "heuristic", heur_conf
    return heu, "heuristic", heur_conf


def main() -> int:
    parser = argparse.ArgumentParser(description="Train lightweight ponder gate learned adjustment weights from JSONL.")
    parser.add_argument("--input", required=True, help="DecisionEvent JSONL input")
    parser.add_argument("--output", default="", help="output ponder_gate_weights JSON path")
    parser.add_argument("--output-dir", default="", help="output directory (default file: ponder_gate_weights.json)")
    parser.add_argument("--summary-out", default="", help="optional summary JSON path")
    parser.add_argument("--label-mode", default="mixed", choices=["heuristic", "supervised", "mixed"])
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    xs: list[dict[str, float]] = []
    ys: list[float] = []
    label_sources: list[str] = []
    label_confidences: list[float] = []
    sample_weights: list[float] = []
    with in_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            y, source, confidence = _resolve_label(rec, args.label_mode)
            if y is None:
                continue
            xs.append(_feature_row(rec))
            ys.append(float(y))
            label_sources.append(source)
            c = _clamp01(confidence)
            label_confidences.append(c)
            source_l = source.lower()
            if source_l in {"runtime_observed", "mixed", "runtime"}:
                base_w = 1.0
            elif source_l == "supervised":
                base_w = 0.8
            else:
                base_w = 0.35
            sample_weights.append(max(0.05, c) * base_w)

    if not xs:
        raise SystemExit("no trainable records")

    total_w = max(1e-6, sum(sample_weights))
    y_mean = sum((w * y) for w, y in zip(sample_weights, ys)) / total_w
    weights: dict[str, float] = {}
    for key in FEATURE_KEYS:
        num = 0.0
        den = 1e-6
        for x, y, w in zip(xs, ys, sample_weights):
            xv = float(x.get(key, 0.0))
            num += w * xv * (y - y_mean)
            den += w * abs(xv)
        weights[key] = max(-0.25, min(0.25, num / den))

    threshold_suggested = 0.55 if y_mean >= 0.5 else 0.60
    runtime_count = sum(1 for s in label_sources if s.lower() in {"runtime_observed", "mixed", "runtime"})
    heuristic_count = sum(1 for s in label_sources if s.lower() == "heuristic")
    payload = {
        "version": 1,
        "kind": "ponder_gate_adjustment",
        "source": "offline_trainer_v1",
        "label_mode": args.label_mode,
        "trained_samples": len(xs),
        "heuristic_samples": sum(1 for x in label_sources if x == "heuristic"),
        "supervised_samples": sum(1 for x in label_sources if x == "supervised"),
        "runtime_samples": runtime_count,
        "runtime_label_ratio": (runtime_count / float(len(xs))) if xs else 0.0,
        "heuristic_label_ratio": (heuristic_count / float(len(xs))) if xs else 0.0,
        "avg_label_confidence": (sum(label_confidences) / float(len(label_confidences))) if label_confidences else 0.0,
        "features_version": PONDER_GATE_FEATURES_VERSION,
        "threshold_suggested": threshold_suggested,
        "weights": weights,
    }

    out_path = _resolve_output_path(
        output=args.output,
        output_dir=args.output_dir,
        filename="ponder_gate_weights.json",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _write_summary(
        args.summary_out,
        {
            "input": str(in_path),
            "output": str(out_path),
            "label_mode": args.label_mode,
            "trained_samples": len(xs),
            "runtime_label_ratio": payload["runtime_label_ratio"],
            "heuristic_label_ratio": payload["heuristic_label_ratio"],
            "avg_label_confidence": payload["avg_label_confidence"],
        },
    )
    print(f"wrote {out_path} samples={len(xs)} label_mode={args.label_mode}")
    return 0


def _resolve_output_path(*, output: str, output_dir: str, filename: str) -> Path:
    out = (output or "").strip()
    if out:
        return Path(out)
    out_dir = (output_dir or "").strip()
    if out_dir:
        return Path(out_dir) / filename
    raise SystemExit("either --output or --output-dir is required")


def _write_summary(path: str, payload: dict[str, Any]) -> None:
    raw = (path or "").strip()
    if not raw:
        return
    out = Path(raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
