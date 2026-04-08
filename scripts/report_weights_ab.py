#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SystemExit(f"invalid json: {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"json must be object: {path}")
    return data


def _weights_map(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("weights")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except Exception:
            continue
    return out


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k != "weights"}


def _metadata_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    diff: dict[str, dict[str, Any]] = {}
    keys = set(a.keys()) | set(b.keys())
    for key in sorted(keys):
        va = a.get(key)
        vb = b.get(key)
        if va != vb:
            diff[key] = {"a": va, "b": vb}
    return diff


def _coef_deltas(a: dict[str, float], b: dict[str, float]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for key in sorted(set(a.keys()) | set(b.keys())):
        av = float(a.get(key, 0.0))
        bv = float(b.get(key, 0.0))
        out[key] = {
            "a": av,
            "b": bv,
            "delta_signed": bv - av,
            "delta_abs": abs(bv - av),
        }
    return out


def _safety_notes(weights_type: str, a_payload: dict[str, Any], b_payload: dict[str, Any], deltas: dict[str, dict[str, float]]) -> list[str]:
    notes: list[str] = []
    kind_a = str(a_payload.get("kind", ""))
    kind_b = str(b_payload.get("kind", ""))
    if kind_a and kind_b and kind_a != kind_b:
        notes.append("kind_mismatch")

    fv_a = str(a_payload.get("features_version", ""))
    fv_b = str(b_payload.get("features_version", ""))
    if fv_a and fv_b and fv_a != fv_b:
        notes.append("feature_version_mismatch")

    high_delta = [k for k, d in deltas.items() if float(d.get("delta_abs", 0.0)) >= 0.15]
    if high_delta:
        notes.append(f"large_coef_shift:{len(high_delta)}")

    if weights_type == "hybrid":
        if "conflict" in deltas and float(deltas["conflict"]["b"]) > 0.0:
            notes.append("hybrid_conflict_positive_check")
    if weights_type == "ponder":
        if "cache_age_ms" in deltas and float(deltas["cache_age_ms"]["b"]) > 0.2:
            notes.append("ponder_cache_age_positive_check")
    return notes


def _render_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Weights A/B Report ({report.get('weights_type')})")
    lines.append("")
    lines.append(f"- keys_added: {len(report.get('keys_added', []))}")
    lines.append(f"- keys_removed: {len(report.get('keys_removed', []))}")
    lines.append(f"- metadata_diff: {len(report.get('metadata_diff', {}))}")
    lines.append("")

    notes = report.get("safety_notes", [])
    lines.append("## Safety Notes")
    if isinstance(notes, list) and notes:
        for n in notes:
            lines.append(f"- {n}")
    else:
        lines.append("- none")
    lines.append("")

    deltas = report.get("coef_deltas", {})
    top = sorted(
        [(k, v) for k, v in deltas.items() if isinstance(v, dict)],
        key=lambda kv: float(kv[1].get("delta_abs", 0.0)),
        reverse=True,
    )[:12]
    lines.append("## Top Coef Deltas")
    lines.append("| key | a | b | delta_signed | delta_abs |")
    lines.append("|---|---:|---:|---:|---:|")
    for k, d in top:
        lines.append(
            f"| {k} | {float(d.get('a', 0.0)):.6f} | {float(d.get('b', 0.0)):.6f} | {float(d.get('delta_signed', 0.0)):.6f} | {float(d.get('delta_abs', 0.0)):.6f} |"
        )
    lines.append("")

    mdiff = report.get("metadata_diff", {})
    lines.append("## Metadata Diff")
    if isinstance(mdiff, dict) and mdiff:
        for k, v in mdiff.items():
            if not isinstance(v, dict):
                continue
            lines.append(f"- {k}: A={v.get('a')} / B={v.get('b')}")
    else:
        lines.append("- none")
    lines.append("")

    eval_block = report.get("actual_game_eval", {})
    if isinstance(eval_block, dict) and bool(eval_block.get("available")):
        lines.append("## Actual Game Eval Diff")
        diff = eval_block.get("diff", {})
        if isinstance(diff, dict):
            for key, value in sorted(diff.items()):
                try:
                    num = float(value)
                    lines.append(f"- {key}: {num:+.6f}")
                except Exception:
                    lines.append(f"- {key}: {value}")
        lines.append("")
    return "\n".join(lines)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v in {float("inf"), float("-inf")}:
        return default
    return v


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
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


def _eval_metrics(path: Path) -> dict[str, float]:
    rows = _iter_jsonl(path)
    total = len(rows)
    if total <= 0:
        return {
            "total_records": 0.0,
            "swing_success_rate": 0.0,
            "swing_fail_rate": 0.0,
            "neutral_rate": 0.0,
            "unknown_rate": 1.0,
            "avg_outcome_confidence": 0.0,
            "actual_move_topk_rate": 0.0,
            "avg_actual_move_rank": 0.0,
            "reuse_then_bestmove_changed_rate": 0.0,
            "winloss_balance": 0.0,
        }

    swing_success = 0
    swing_fail = 0
    neutral = 0
    unknown = 0
    conf_sum = 0.0
    topk_true = 0
    rank_sum = 0.0
    rank_n = 0
    reuse_changed = 0

    for rec in rows:
        tag = str(rec.get("outcome_tag", "")).strip().lower()
        if tag in {"win", "swing_success"}:
            swing_success += 1
        elif tag in {"loss", "swing_fail"}:
            swing_fail += 1
        elif tag == "neutral":
            neutral += 1
        else:
            unknown += 1

        conf_sum += _safe_float(rec.get("outcome_confidence"), 0.0)
        if bool(rec.get("actual_move_in_reply_topk", False)):
            topk_true += 1
        rank = rec.get("actual_move_rank_in_reply_topk")
        try:
            r = int(rank)
            if r > 0:
                rank_sum += float(r)
                rank_n += 1
        except Exception:
            pass
        if bool(rec.get("reuse_then_bestmove_changed", False)):
            reuse_changed += 1

    n = float(total)
    return {
        "total_records": n,
        "swing_success_rate": swing_success / n,
        "swing_fail_rate": swing_fail / n,
        "neutral_rate": neutral / n,
        "unknown_rate": unknown / n,
        "avg_outcome_confidence": conf_sum / n,
        "actual_move_topk_rate": topk_true / n,
        "avg_actual_move_rank": (rank_sum / float(rank_n)) if rank_n > 0 else 0.0,
        "reuse_then_bestmove_changed_rate": reuse_changed / n,
        "winloss_balance": (swing_success - swing_fail) / n,
    }


def _eval_diff(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    keys = set(a.keys()) | set(b.keys())
    out: dict[str, float] = {}
    for key in sorted(keys):
        out[key] = _safe_float(b.get(key), 0.0) - _safe_float(a.get(key), 0.0)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two weights JSON files (A/B) and emit diff report.")
    parser.add_argument("--a", required=True, help="weights A JSON path")
    parser.add_argument("--b", required=True, help="weights B JSON path")
    parser.add_argument("--type", required=True, choices=["hybrid", "ponder"], help="weights type")
    parser.add_argument("--out", required=True, help="output report JSON path")
    parser.add_argument("--md-out", default="", help="optional output markdown path")
    parser.add_argument("--eval-log-a", default="", help="optional evaluation JSONL for A")
    parser.add_argument("--eval-log-b", default="", help="optional evaluation JSONL for B")
    args = parser.parse_args()

    path_a = Path(args.a)
    path_b = Path(args.b)
    if not path_a.exists():
        raise SystemExit(f"missing --a file: {path_a}")
    if not path_b.exists():
        raise SystemExit(f"missing --b file: {path_b}")

    payload_a = _load_json(path_a)
    payload_b = _load_json(path_b)
    weights_a = _weights_map(payload_a)
    weights_b = _weights_map(payload_b)

    keys_added = sorted([k for k in weights_b.keys() if k not in weights_a])
    keys_removed = sorted([k for k in weights_a.keys() if k not in weights_b])
    deltas = _coef_deltas(weights_a, weights_b)
    metadata_diff = _metadata_diff(_metadata(payload_a), _metadata(payload_b))
    report = {
        "weights_type": args.type,
        "a_path": str(path_a),
        "b_path": str(path_b),
        "keys_added": keys_added,
        "keys_removed": keys_removed,
        "coef_deltas": deltas,
        "metadata_diff": metadata_diff,
        "safety_notes": _safety_notes(args.type, payload_a, payload_b, deltas),
    }

    eval_a_raw = (args.eval_log_a or "").strip()
    eval_b_raw = (args.eval_log_b or "").strip()
    if eval_a_raw and eval_b_raw:
        eval_a_path = Path(eval_a_raw)
        eval_b_path = Path(eval_b_raw)
        metrics_a = _eval_metrics(eval_a_path)
        metrics_b = _eval_metrics(eval_b_path)
        diff = _eval_diff(metrics_a, metrics_b)
        report["actual_game_eval"] = {
            "available": True,
            "a_path": str(eval_a_path),
            "b_path": str(eval_b_path),
            "a": metrics_a,
            "b": metrics_b,
            "diff": diff,
        }
        if int(metrics_a.get("total_records", 0)) < 10 or int(metrics_b.get("total_records", 0)) < 10:
            report["safety_notes"].append("low_eval_sample_count")
    else:
        report["actual_game_eval"] = {"available": False}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.md_out:
        md_path = Path(args.md_out)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(_render_md(report), encoding="utf-8")

    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
