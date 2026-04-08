#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v in {float("inf"), float("-inf")}:
        return default
    return v


def _is_known_outcome(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower() not in {"", "unknown", "none", "null"}


def _iter_records(paths: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    return out


def _source_key(rec: dict[str, Any]) -> str:
    raw = rec.get("source_log_path")
    if not isinstance(raw, str) or not raw.strip():
        raw = rec.get("_source_log_path")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "<unknown>"


def _drop_reasons(rec: dict[str, Any], *, min_ponder_conf: float, min_outcome_conf: float) -> list[str]:
    reasons: list[str] = []
    ponder_target = rec.get("ponder_reuse_target")
    if ponder_target is None:
        reasons.append("ponder_target_missing")
    pconf = _safe_float(rec.get("ponder_label_confidence"), 0.0)
    if pconf < min_ponder_conf:
        reasons.append("ponder_conf_low")

    actual_move = rec.get("actual_opponent_move")
    if not isinstance(actual_move, str) or not actual_move.strip():
        reasons.append("actual_move_missing")

    oconf = _safe_float(rec.get("outcome_confidence"), 0.0)
    if oconf < min_outcome_conf:
        reasons.append("outcome_conf_low")

    if not _is_known_outcome(rec.get("outcome_tag")):
        reasons.append("outcome_unknown")
    return reasons


def build_quality_report(
    records: list[dict[str, Any]],
    *,
    min_ponder_conf: float = 0.0,
    min_outcome_conf: float = 0.0,
) -> dict[str, Any]:
    total = len(records)
    source_counts: Counter[str] = Counter()
    drop_reasons: Counter[str] = Counter()

    conf_sum = 0.0
    changed_count = 0
    actual_count = 0
    outcome_count = 0
    dfpn_unknown = 0
    eligible_ponder = 0
    eligible_hybrid = 0
    by_source_raw: dict[str, dict[str, Any]] = {}

    for rec in records:
        source = str(rec.get("ponder_label_source", "heuristic") or "heuristic")
        source_counts[source] += 1
        src_key = _source_key(rec)
        src = by_source_raw.setdefault(
            src_key,
            {
                "total_records": 0,
                "ponder_label_source_counts": Counter(),
                "ponder_conf_sum": 0.0,
                "changed_count": 0,
                "actual_count": 0,
                "outcome_count": 0,
                "dfpn_unknown": 0,
                "eligible_ponder": 0,
                "eligible_hybrid": 0,
                "drop_reasons": Counter(),
            },
        )
        src["total_records"] += 1

        pconf = max(0.0, min(1.0, _safe_float(rec.get("ponder_label_confidence"), 0.0)))
        conf_sum += pconf
        src["ponder_label_source_counts"][source] += 1
        src["ponder_conf_sum"] += pconf

        if bool(rec.get("reuse_then_bestmove_changed", False)):
            changed_count += 1
            src["changed_count"] += 1

        if isinstance(rec.get("actual_opponent_move"), str) and str(rec.get("actual_opponent_move")).strip():
            actual_count += 1
            src["actual_count"] += 1
        if _is_known_outcome(rec.get("outcome_tag")):
            outcome_count += 1
            src["outcome_count"] += 1

        unknown_count = int(_safe_float(rec.get("dfpn_parse_unknown_count"), 0.0))
        if unknown_count > 0 or str(rec.get("dfpn_status_summary", "")).lower() == "unknown":
            dfpn_unknown += 1
            src["dfpn_unknown"] += 1

        p_target = rec.get("ponder_reuse_target")
        if p_target is not None and pconf >= min_ponder_conf:
            eligible_ponder += 1
            src["eligible_ponder"] += 1

        h_label = rec.get("label")
        h_outcome_conf = _safe_float(rec.get("outcome_confidence"), 0.0)
        if h_label is not None and h_outcome_conf >= min_outcome_conf:
            eligible_hybrid += 1
            src["eligible_hybrid"] += 1

        rec_drop_reasons = _drop_reasons(rec, min_ponder_conf=min_ponder_conf, min_outcome_conf=min_outcome_conf)
        for reason in rec_drop_reasons:
            drop_reasons[reason] += 1
            src["drop_reasons"][reason] += 1

    runtime_ratio = (source_counts.get("runtime_observed", 0) / float(total)) if total else 0.0
    heuristic_ratio = (source_counts.get("heuristic", 0) / float(total)) if total else 0.0
    by_source: dict[str, dict[str, Any]] = {}
    for src_key, src in by_source_raw.items():
        src_total = int(src["total_records"])
        src_counts: Counter[str] = src["ponder_label_source_counts"]
        by_source[src_key] = {
            "total_records": src_total,
            "ponder_label_source_counts": dict(src_counts),
            "runtime_label_ratio": (src_counts.get("runtime_observed", 0) / float(src_total)) if src_total else 0.0,
            "heuristic_label_ratio": (src_counts.get("heuristic", 0) / float(src_total)) if src_total else 0.0,
            "avg_ponder_label_confidence": (float(src["ponder_conf_sum"]) / float(src_total)) if src_total else 0.0,
            "reuse_then_bestmove_changed_rate": (int(src["changed_count"]) / float(src_total)) if src_total else 0.0,
            "actual_opponent_move_coverage": (int(src["actual_count"]) / float(src_total)) if src_total else 0.0,
            "outcome_tag_coverage": (int(src["outcome_count"]) / float(src_total)) if src_total else 0.0,
            "dfpn_parse_unknown_rate": (int(src["dfpn_unknown"]) / float(src_total)) if src_total else 0.0,
            "eligible_for_ponder_training": int(src["eligible_ponder"]),
            "eligible_for_hybrid_training": int(src["eligible_hybrid"]),
            "drop_reasons": dict(src["drop_reasons"]),
        }

    report = {
        "total_records": total,
        "ponder_label_source_counts": dict(source_counts),
        "runtime_label_ratio": runtime_ratio,
        "heuristic_label_ratio": heuristic_ratio,
        "avg_ponder_label_confidence": (conf_sum / float(total)) if total else 0.0,
        "reuse_then_bestmove_changed_rate": (changed_count / float(total)) if total else 0.0,
        "actual_opponent_move_coverage": (actual_count / float(total)) if total else 0.0,
        "outcome_tag_coverage": (outcome_count / float(total)) if total else 0.0,
        "dfpn_parse_unknown_rate": (dfpn_unknown / float(total)) if total else 0.0,
        "eligible_for_ponder_training": eligible_ponder,
        "eligible_for_hybrid_training": eligible_hybrid,
        "drop_reasons": dict(drop_reasons),
        "records_used_for_ponder": eligible_ponder,
        "records_used_for_hybrid": eligible_hybrid,
        "records_dropped": max(0, total - max(eligible_ponder, eligible_hybrid)),
        "by_source": by_source,
    }
    return report


def _text_report(report: dict[str, Any]) -> str:
    lines = [
        f"total_records={report['total_records']}",
        f"runtime_label_ratio={report['runtime_label_ratio']:.4f}",
        f"heuristic_label_ratio={report['heuristic_label_ratio']:.4f}",
        f"avg_ponder_label_confidence={report['avg_ponder_label_confidence']:.4f}",
        f"reuse_then_bestmove_changed_rate={report['reuse_then_bestmove_changed_rate']:.4f}",
        f"actual_opponent_move_coverage={report['actual_opponent_move_coverage']:.4f}",
        f"outcome_tag_coverage={report['outcome_tag_coverage']:.4f}",
        f"dfpn_parse_unknown_rate={report['dfpn_parse_unknown_rate']:.4f}",
        f"eligible_for_ponder_training={report['eligible_for_ponder_training']}",
        f"eligible_for_hybrid_training={report['eligible_for_hybrid_training']}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Report learning-data quality for TASO-SWINDLE logs.")
    parser.add_argument("--input", nargs="+", required=True, help="input JSONL file(s)")
    parser.add_argument("--output-json", default="", help="output report JSON path")
    parser.add_argument("--output-text", default="", help="output human-readable report text path")
    parser.add_argument("--min-ponder-label-confidence", type=float, default=0.0)
    parser.add_argument("--min-outcome-confidence", type=float, default=0.0)
    args = parser.parse_args()

    inputs = [Path(p) for p in args.input]
    records = _iter_records(inputs)
    report = build_quality_report(
        records,
        min_ponder_conf=max(0.0, min(1.0, float(args.min_ponder_label_confidence))),
        min_outcome_conf=max(0.0, min(1.0, float(args.min_outcome_confidence))),
    )

    text = _text_report(report)
    print(text)

    if args.output_json:
        out_json = Path(args.output_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        with out_json.open("w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    if args.output_text:
        out_txt = Path(args.output_text)
        out_txt.parent.mkdir(parents=True, exist_ok=True)
        out_txt.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
