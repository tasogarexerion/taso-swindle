#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from project_policies import anonymize_learning_sample


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v in {float("inf"), float("-inf")}:
        return default
    return v


def _clamp01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _safe_float(value, default)))


def _extract_selected_candidate(rec: dict[str, Any]) -> Optional[dict[str, Any]]:
    final = str(rec.get("final_bestmove", ""))
    cands = rec.get("candidates")
    if not isinstance(cands, list):
        return None
    for cand in cands:
        if isinstance(cand, dict) and str(cand.get("move", "")) == final:
            return cand
    if cands and isinstance(cands[0], dict):
        return cands[0]
    return None


def _reply_rank(reply_topk: list[dict[str, Any]], actual_move: str) -> Optional[int]:
    for idx, rep in enumerate(reply_topk):
        move = str(rep.get("move", "")) if isinstance(rep, dict) else ""
        if move == actual_move:
            return idx + 1
    return None


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


def supervised_label(outcome_tag: Optional[str], in_topk: Optional[bool], rank: Optional[int]) -> Optional[float]:
    tag = (outcome_tag or "").strip().lower()
    if tag in {"win", "swing_success"}:
        return 1.0
    if tag in {"loss", "swing_fail"}:
        return 0.0
    if tag == "draw":
        return 0.5
    if in_topk is True:
        if rank == 1:
            return 0.2
        if isinstance(rank, int) and rank > 1:
            return min(0.75, 0.35 + (0.08 * float(rank)))
        return 0.4
    if in_topk is False:
        return 0.7
    return None


def resolve_label(
    label_mode: str,
    rec: dict[str, Any],
    *,
    in_topk: Optional[bool],
    rank: Optional[int],
    prefer_labeled_outcome: bool = False,
    min_outcome_confidence: float = 0.0,
    min_outcome_match_confidence: float = 0.0,
    require_actual_move: bool = False,
) -> tuple[Optional[float], str]:
    lm = label_mode.lower()
    actual_move = rec.get("actual_opponent_move")
    if require_actual_move and (not isinstance(actual_move, str) or not actual_move.strip()):
        sup = None
    else:
        sup = supervised_label(rec.get("outcome_tag"), in_topk, rank)
    outcome_conf = rec.get("outcome_confidence")
    try:
        if sup is not None and float(outcome_conf) < max(0.0, min(1.0, min_outcome_confidence)):
            sup = None
    except Exception:
        if sup is not None and min_outcome_confidence > 0.0:
            sup = None
    outcome_match_conf = rec.get("outcome_match_confidence")
    try:
        if sup is not None and float(outcome_match_conf) < max(0.0, min(1.0, min_outcome_match_confidence)):
            sup = None
    except Exception:
        if sup is not None and min_outcome_match_confidence > 0.0:
            sup = None
    pse = pseudo_label(rec)

    if prefer_labeled_outcome and sup is not None:
        return sup, "supervised_labeled"
    if lm == "supervised":
        return sup, "supervised"
    if lm == "mixed":
        if sup is not None:
            return sup, "supervised"
        return pse, "pseudo"
    return pse, "pseudo"


def ponder_label_heuristic(rec: dict[str, Any]) -> tuple[Optional[float], float]:
    cache_hit = bool(rec.get("ponder_cache_hit", False))
    cache_used = bool(rec.get("ponder_cache_used", False))
    gate_reason = str(rec.get("ponder_cache_gate_reason") or "")
    selected = str(rec.get("selected_reason") or "")
    restarts = int(_safe_float(rec.get("backend_restart_count"), 0.0))
    status = str(rec.get("ponder_status_summary", "not_used"))

    if cache_hit and (not cache_used) and gate_reason in {"quality_gate", "mate_without_verify", "stale", "position_miss"}:
        return 0.0, 0.35
    if cache_used:
        if selected in {"ponder_fallback", "fallback_backend", "fallback_resign"} or restarts > 0:
            return 0.0, 0.45
        return 1.0, 0.45
    if status in {"fallback", "timeout", "error"}:
        return 0.0, 0.30
    return None, 0.0


def ponder_label_runtime(rec: dict[str, Any]) -> tuple[Optional[float], float]:
    source = str(rec.get("ponder_label_source", "")).lower()
    cache_used = bool(rec.get("ponder_cache_used", False))
    changed = rec.get("reuse_then_bestmove_changed")
    if source not in {"runtime_observed", "mixed"} and not cache_used:
        return None, 0.0
    if not isinstance(changed, bool):
        return None, 0.0
    label = 0.0 if changed else 1.0
    conf = _clamp01(rec.get("ponder_label_confidence"), default=0.9)
    if conf <= 0.0:
        conf = 0.9
    return label, conf


def resolve_ponder_label(
    rec: dict[str, Any],
    *,
    ponder_label_mode: str,
    min_confidence: float,
) -> tuple[Optional[float], str, float]:
    mode = (ponder_label_mode or "runtime_first").strip().lower()
    runtime_label, runtime_conf = ponder_label_runtime(rec)
    heur_label, heur_conf = ponder_label_heuristic(rec)

    label: Optional[float] = None
    source = "heuristic"
    conf = 0.0
    if mode == "heuristic_only":
        label, conf = heur_label, heur_conf
        source = "heuristic"
    elif mode == "mixed":
        if runtime_label is not None:
            label, conf = runtime_label, runtime_conf
            source = "mixed"
        else:
            label, conf = heur_label, heur_conf
            source = "mixed"
    else:  # runtime_first
        if runtime_label is not None:
            label, conf = runtime_label, runtime_conf
            source = "runtime_observed"
        else:
            label, conf = heur_label, heur_conf
            source = "heuristic"

    conf = _clamp01(conf)
    if label is None:
        return None, source, conf
    if conf < max(0.0, min(1.0, float(min_confidence))):
        return None, source, conf
    return max(0.0, min(1.0, float(label))), source, conf


def main() -> int:
    parser = argparse.ArgumentParser(description="Build training labels from TASO-SWINDLE DecisionEvent JSONL")
    parser.add_argument("--input", required=True, help="input DecisionEvent JSONL")
    parser.add_argument("--output", default="", help="output labeled JSONL")
    parser.add_argument("--output-dir", default="", help="output directory (default file: training_labels.jsonl)")
    parser.add_argument("--summary-out", default="", help="optional summary JSON path")
    parser.add_argument("--label-mode", default="mixed", choices=["pseudo", "supervised", "mixed"])
    parser.add_argument("--skip-unlabeled", action="store_true", help="skip records without label")
    parser.add_argument("--prefer-labeled-outcome", action="store_true", help="prefer supervised outcome label when available")
    parser.add_argument("--min-outcome-confidence", type=float, default=0.0, help="minimum outcome_confidence for supervised labels")
    parser.add_argument("--min-outcome-match-confidence", type=float, default=0.0, help="minimum outcome_match_confidence for supervised labels")
    parser.add_argument("--require-actual-move", action="store_true", help="require actual_opponent_move to emit supervised labels")
    parser.add_argument(
        "--ponder-label-mode",
        default="runtime_first",
        choices=["heuristic_only", "runtime_first", "mixed"],
        help="ponder label priority",
    )
    parser.add_argument(
        "--min-ponder-label-confidence",
        type=float,
        default=0.0,
        help="minimum confidence for ponder labels",
    )
    parser.add_argument(
        "--anonymize-personal-info",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="anonymize personal information fields in emitted training samples",
    )
    parser.add_argument(
        "--anonymize-salt",
        default="",
        help="optional salt for deterministic anonymization tokens",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    out_path = _resolve_output_path(output=args.output, output_dir=args.output_dir, filename="training_labels.jsonl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    written = 0
    with in_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for raw in src:
            line = raw.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue

            actual_move = rec.get("actual_opponent_move")
            in_topk = rec.get("actual_move_in_reply_topk")
            rank = rec.get("actual_move_rank_in_reply_topk")

            selected = _extract_selected_candidate(rec)
            reply_topk: list[dict[str, Any]] = []
            if isinstance(selected, dict):
                raw_topk = selected.get("reply_topk")
                if isinstance(raw_topk, list):
                    reply_topk = [x for x in raw_topk if isinstance(x, dict)]

            if isinstance(actual_move, str) and actual_move:
                if in_topk is None:
                    in_topk = _reply_rank(reply_topk, actual_move) is not None
                if rank is None:
                    rank = _reply_rank(reply_topk, actual_move)

            label, label_source = resolve_label(
                args.label_mode,
                rec,
                in_topk=in_topk,
                rank=rank,
                prefer_labeled_outcome=args.prefer_labeled_outcome,
                min_outcome_confidence=float(args.min_outcome_confidence),
                min_outcome_match_confidence=float(args.min_outcome_match_confidence),
                require_actual_move=args.require_actual_move,
            )
            ponder_target, ponder_source, ponder_conf = resolve_ponder_label(
                rec,
                ponder_label_mode=args.ponder_label_mode,
                min_confidence=float(args.min_ponder_label_confidence),
            )
            if label is None and args.skip_unlabeled:
                continue

            out_rec = {
                "timestamp": rec.get("timestamp"),
                "game_id": rec.get("game_id"),
                "ply": rec.get("ply"),
                "search_id": rec.get("search_id"),
                "label": label,
                "label_source": label_source if label is not None else "none",
                "actual_opponent_move": actual_move,
                "actual_move_in_reply_topk": in_topk,
                "actual_move_rank_in_reply_topk": rank,
                "outcome_tag": rec.get("outcome_tag"),
                "outcome_confidence": rec.get("outcome_confidence"),
                "outcome_match_source": rec.get("outcome_match_source"),
                "outcome_match_confidence": rec.get("outcome_match_confidence"),
                "outcome_match_candidates": rec.get("outcome_match_candidates"),
                "game_id_raw": rec.get("game_id_raw"),
                "game_id_normalized": rec.get("game_id_normalized"),
                "game_id_source_detected": rec.get("game_id_source_detected"),
                "source_log_path": rec.get("_source_log_path"),
                "verify_mode_used": rec.get("verify_mode_used"),
                "verify_status_summary": rec.get("verify_status_summary"),
                "dfpn_status_summary": rec.get("dfpn_status_summary"),
                "dfpn_parser_mode": rec.get("dfpn_parser_mode"),
                "dfpn_parser_hits": rec.get("dfpn_parser_hits", []),
                "verify_conflict_count": rec.get("verify_conflict_count", 0),
                "verify_unknown_count": rec.get("verify_unknown_count", 0),
                "emergency_fast_mode": rec.get("emergency_fast_mode", False),
                "selected_reason": rec.get("selected_reason"),
                "ponder_reuse_target": ponder_target,
                "ponder_label_source": ponder_source,
                "ponder_label_confidence": ponder_conf,
                "reuse_then_bestmove_changed": rec.get("reuse_then_bestmove_changed", False),
                "ponder_reuse_decision_id": rec.get("ponder_reuse_decision_id"),
                "ponder_reuse_parent_position_key": rec.get("ponder_reuse_parent_position_key"),
            }
            out_rec = anonymize_learning_sample(
                out_rec,
                enabled=bool(args.anonymize_personal_info),
                salt=str(args.anonymize_salt),
            )
            dst.write(json.dumps(out_rec, ensure_ascii=False) + "\n")
            written += 1

    summary = {
        "input": str(in_path),
        "output": str(out_path),
        "records_raw": total,
        "records_training": written,
        "label_mode": args.label_mode,
        "ponder_label_mode": args.ponder_label_mode,
        "min_outcome_confidence": float(args.min_outcome_confidence),
        "min_outcome_match_confidence": float(args.min_outcome_match_confidence),
        "min_ponder_label_confidence": float(args.min_ponder_label_confidence),
        "anonymize_personal_info": bool(args.anonymize_personal_info),
        "prefer_labeled_outcome": bool(args.prefer_labeled_outcome),
        "require_actual_move": bool(args.require_actual_move),
    }
    _write_summary(args.summary_out, summary)

    print(f"wrote {out_path} records={written}/{total}")
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
