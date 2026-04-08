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

from taso_swindle.swindle.weight_tuner import HYBRID_FEATURES_VERSION


FEATURE_KEYS = [
    "bias",
    "agree",
    "conflict",
    "verifier_for_us",
    "verifier_for_them",
    "dfpn_for_us",
    "dfpn_for_them",
    "verifier_conf",
    "dfpn_conf",
    "distance_available",
    "strict_hit",
    "loose_hit",
    "actual_in_topk",
    "actual_rank_inv",
    "outcome_win",
    "outcome_loss",
    "outcome_draw",
    "mode_top",
    "mode_aggressive",
    "parser_strict",
    "parser_loose",
    "emergency",
]


def build_features(rec: dict[str, Any]) -> dict[str, float]:
    verify_status = str(rec.get("verify_status_summary", "not_used"))
    dfpn_status = str(rec.get("dfpn_status_summary", "not_used"))
    verify_mode = str(rec.get("verify_mode_used", "VERIFY_ONLY")).upper()
    parser_mode = str(rec.get("dfpn_parser_mode", "AUTO")).upper()
    hits = [str(x) for x in rec.get("dfpn_parser_hits", []) if isinstance(x, str)]

    agree = float(
        (verify_status == "confirmed" and dfpn_status == "confirmed")
        or (verify_status == "rejected" and dfpn_status == "rejected")
    )
    conflict = float(int(rec.get("verify_conflict_count", 0)) > 0)
    strict_hit = float(any(":strict:" in h or "strict:" in h for h in hits))
    loose_hit = float(any(":loose:" in h or "loose:" in h for h in hits))
    verifier_conf = 0.85 if verify_status == "confirmed" else (0.75 if verify_status == "rejected" else 0.30)
    dfpn_conf = 0.80 if dfpn_status == "confirmed" else (0.70 if dfpn_status == "rejected" else 0.25)

    actual_in_topk = rec.get("actual_move_in_reply_topk")
    actual_rank = rec.get("actual_move_rank_in_reply_topk")
    outcome_tag = str(rec.get("outcome_tag", "")).lower()

    return {
        "bias": 1.0,
        "agree": agree,
        "conflict": conflict,
        "verifier_for_us": 1.0 if verify_status == "confirmed" else 0.0,
        "verifier_for_them": 1.0 if verify_status == "rejected" else 0.0,
        "dfpn_for_us": 1.0 if dfpn_status == "confirmed" else 0.0,
        "dfpn_for_them": 1.0 if dfpn_status == "rejected" else 0.0,
        "verifier_conf": verifier_conf,
        "dfpn_conf": dfpn_conf,
        "distance_available": 1.0 if int(rec.get("dfpn_distance_available_count", 0)) > 0 else 0.0,
        "strict_hit": strict_hit,
        "loose_hit": loose_hit,
        "actual_in_topk": 1.0 if bool(actual_in_topk) else 0.0,
        "actual_rank_inv": _rank_inverse(actual_rank),
        "outcome_win": 1.0 if outcome_tag in {"win", "swing_success"} else 0.0,
        "outcome_loss": 1.0 if outcome_tag in {"loss", "swing_fail"} else 0.0,
        "outcome_draw": 1.0 if outcome_tag == "draw" else 0.0,
        "mode_top": 1.0 if verify_mode == "TOP_CANDIDATES" else 0.0,
        "mode_aggressive": 1.0 if verify_mode == "AGGRESSIVE" else 0.0,
        "parser_strict": 1.0 if parser_mode == "STRICT" else 0.0,
        "parser_loose": 1.0 if parser_mode == "LOOSE" else 0.0,
        "emergency": 1.0 if bool(rec.get("emergency_fast_mode", False)) else 0.0,
    }


def pseudo_label(rec: dict[str, Any]) -> Optional[float]:
    selected_reason = str(rec.get("selected_reason", ""))
    verify_status = str(rec.get("verify_status_summary", "not_used"))
    dfpn_status = str(rec.get("dfpn_status_summary", "not_used"))

    if verify_status == "rejected" or dfpn_status == "rejected":
        return 0.0
    if selected_reason == "mate_priority" and verify_status in {"confirmed", "not_used", "unknown"}:
        return 1.0
    if selected_reason == "fallback_backend" and verify_status in {"timeout", "error", "skipped"}:
        return 0.25
    if selected_reason in {"rev_max", "mate_priority"}:
        return 0.65
    return None


def supervised_label(rec: dict[str, Any]) -> Optional[float]:
    tag = str(rec.get("outcome_tag", "")).strip().lower()
    if tag in {"win", "swing_success"}:
        return 1.0
    if tag in {"loss", "swing_fail"}:
        return 0.0
    if tag == "draw":
        return 0.5

    in_topk = rec.get("actual_move_in_reply_topk")
    rank = rec.get("actual_move_rank_in_reply_topk")
    if in_topk is True:
        if rank == 1:
            return 0.2
        if isinstance(rank, int) and rank > 1:
            return min(0.75, 0.35 + (0.08 * float(rank)))
        return 0.4
    if in_topk is False:
        return 0.7
    return None


def resolve_label(rec: dict[str, Any], label_mode: str) -> tuple[Optional[float], str]:
    lm = label_mode.lower()
    sup = supervised_label(rec)
    pse = pseudo_label(rec)

    if lm == "supervised":
        return sup, "supervised"
    if lm == "mixed":
        if sup is not None:
            return sup, "supervised"
        return pse, "pseudo"
    return pse, "pseudo"


def main() -> int:
    parser = argparse.ArgumentParser(description="Train lightweight hybrid confidence adjustment weights from JSONL logs.")
    parser.add_argument("--input", required=True, help="path to DecisionEvent JSONL")
    parser.add_argument("--output", default="", help="output weights JSON path")
    parser.add_argument("--output-dir", default="", help="output directory (default file: hybrid_weights.json)")
    parser.add_argument("--summary-out", default="", help="optional summary JSON path")
    parser.add_argument("--label-mode", default="pseudo", choices=["pseudo", "supervised", "mixed"])
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = _resolve_output_path(
        output=args.output,
        output_dir=args.output_dir,
        filename="hybrid_weights.json",
    )
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    # Stream aggregates to avoid keeping millions of feature rows in RAM.
    # We only need these sums for the final weight formula:
    #   w = (sum(x*y) - y_mean*sum(x)) / (eps + sum(|x|))
    y_sum = 0.0
    n_samples = 0
    pseudo_samples = 0
    supervised_samples = 0
    sum_xy: dict[str, float] = {k: 0.0 for k in FEATURE_KEYS}
    sum_x: dict[str, float] = {k: 0.0 for k in FEATURE_KEYS}
    sum_abs_x: dict[str, float] = {k: 0.0 for k in FEATURE_KEYS}

    with in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            y, source = resolve_label(rec, args.label_mode)
            if y is None:
                continue
            x = build_features(rec)
            yy = float(y)
            n_samples += 1
            y_sum += yy
            if source == "pseudo":
                pseudo_samples += 1
            elif source == "supervised":
                supervised_samples += 1

            for key in FEATURE_KEYS:
                xv = float(x.get(key, 0.0))
                sum_xy[key] += xv * yy
                sum_x[key] += xv
                sum_abs_x[key] += abs(xv)

    if n_samples <= 0:
        raise SystemExit("no trainable records")

    y_mean = y_sum / float(n_samples)
    weights: dict[str, float] = {}
    for key in FEATURE_KEYS:
        den = 1e-6 + float(sum_abs_x.get(key, 0.0))
        num = float(sum_xy.get(key, 0.0)) - y_mean * float(sum_x.get(key, 0.0))
        weights[key] = max(-0.20, min(0.20, num / den))

    payload = {
        "version": 2,
        "kind": "hybrid_adjustment",
        "source": "offline_trainer_v2",
        "label_mode": args.label_mode,
        "trained_samples": n_samples,
        "pseudo_samples": pseudo_samples,
        "supervised_samples": supervised_samples,
        "features_version": HYBRID_FEATURES_VERSION,
        "weights": weights,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _write_summary(
        args.summary_out,
        {
            "input": str(in_path),
            "output": str(out_path),
            "label_mode": args.label_mode,
            "trained_samples": n_samples,
            "pseudo_samples": payload["pseudo_samples"],
            "supervised_samples": payload["supervised_samples"],
        },
    )
    print(f"wrote {out_path} samples={n_samples} label_mode={args.label_mode}")
    return 0


def _rank_inverse(value: Any) -> float:
    try:
        rank = int(value)
    except Exception:
        return 0.0
    if rank <= 0:
        return 0.0
    return 1.0 / float(rank)


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
