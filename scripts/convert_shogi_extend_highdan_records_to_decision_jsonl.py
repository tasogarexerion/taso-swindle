#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional


KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_dan(raw: str) -> int:
    s = str(raw or "").strip()
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    if digits and "段" in s:
        try:
            return int(digits)
        except Exception:
            return 0
    for k, v in KANJI_NUM.items():
        if f"{k}段" in s:
            return v
    return 0


def _parse_position_and_moves(sfen_body: str) -> tuple[str, list[str]]:
    raw = (sfen_body or "").strip()
    if not raw:
        return "position startpos", []
    marker = " moves "
    if marker not in raw:
        return raw, []
    head, tail = raw.split(marker, 1)
    moves = [tok.strip() for tok in tail.split() if tok.strip()]
    return head.strip(), moves


def _root_position_cmd(prefix: str, moves: list[str], idx: int) -> str:
    if idx <= 0:
        return prefix
    return f"{prefix} moves {' '.join(moves[:idx])}"


def _side_of_ply(idx: int) -> str:
    return "black" if (idx % 2 == 0) else "white"


def _to_outcome_tag(judge: str) -> tuple[str, float]:
    j = (judge or "").strip().lower()
    if j == "win":
        return "win", 0.95
    if j == "lose":
        return "loss", 0.95
    if j in {"draw", "jishogi", "dchikiwake"}:
        return "draw", 0.85
    return "unknown", 0.2


def _iter_targets(rec: dict[str, Any], min_dan: int, max_dan: int, excludes: set[str]) -> list[tuple[str, int, str, str]]:
    out: list[tuple[str, int, str, str]] = []
    memberships = rec.get("memberships")
    if not isinstance(memberships, list):
        return out
    for row in memberships:
        if not isinstance(row, dict):
            continue
        side = str(row.get("location_key", "")).strip().lower()
        if side not in {"black", "white"}:
            continue
        user = row.get("user")
        if not isinstance(user, dict):
            continue
        user_key = str(user.get("key", "")).strip()
        if not user_key or user_key in excludes:
            continue
        grade = row.get("grade_info")
        grade_name = ""
        if isinstance(grade, dict):
            grade_name = str(grade.get("name", "")).strip()
        dan = _parse_dan(grade_name)
        if dan < min_dan:
            continue
        if max_dan > 0 and dan > max_dan:
            continue
        judge = str(row.get("judge_key", "")).strip().lower()
        out.append((user_key, dan, side, judge))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert shogi-extend records into DecisionEvent-like JSONL for high-dan users.")
    parser.add_argument("--input", required=True, help="records_dedup.jsonl or records.jsonl")
    parser.add_argument("--output", required=True, help="DecisionEvent-like output JSONL")
    parser.add_argument("--min-dan", type=int, default=6)
    parser.add_argument("--max-dan", type=int, default=0, help="0=unlimited")
    parser.add_argument("--exclude-user", action="append", default=[])
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    excludes = {str(x).strip() for x in list(args.exclude_user) if str(x).strip()}

    total_games = 0
    used_games = 0
    emitted = 0
    target_users: set[str] = set()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with in_path.open("r", encoding="utf-8") as src, out_path.open("w", encoding="utf-8") as dst:
        for raw in src:
            line = raw.strip()
            if not line:
                continue
            total_games += 1
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue

            game_id = str(rec.get("key", "")).strip()
            if not game_id:
                continue
            prefix, moves = _parse_position_and_moves(str(rec.get("sfen_body", "")))
            if not moves:
                continue
            timestamp = str(rec.get("battled_at", "")).strip()
            if not timestamp:
                timestamp = str(rec.get("timestamp", "")).strip()

            targets = _iter_targets(
                rec,
                min_dan=max(1, int(args.min_dan)),
                max_dan=max(0, int(args.max_dan)),
                excludes=excludes,
            )
            if not targets:
                continue
            used_games += 1

            for user_key, dan, side, judge in targets:
                target_users.add(user_key)
                outcome_tag, outcome_conf = _to_outcome_tag(judge)
                for idx, move in enumerate(moves):
                    if _side_of_ply(idx) != side:
                        continue
                    actual_reply = moves[idx + 1] if (idx + 1) < len(moves) else None
                    row: dict[str, Any] = {
                        "timestamp": timestamp,
                        "game_id": game_id,
                        "ply": idx + 1,
                        "root_sfen": _root_position_cmd(prefix, moves, idx),
                        "root_eval_cp": None,
                        "root_mate": None,
                        "swindle_enabled": True,
                        "mode": "HYBRID",
                        "normal_bestmove": move,
                        "final_bestmove": move,
                        "candidates": [],
                        "selected_reason": "external_highdan_replay",
                        "backend_engine_info": {"name": "external_kifu", "author": user_key, "dan": dan},
                        "emergency_fast_mode": False,
                        "events": [],
                        "option_restore_failed": False,
                        "mate_verify_status": "not_used",
                        "verify_status_summary": "not_used",
                        "verify_mode_used": "VERIFY_ONLY",
                        "verify_engine_kind": "backend",
                        "mate_verify_candidates_count": 0,
                        "dfpn_used": False,
                        "dfpn_status_summary": "not_used",
                        "dfpn_parser_hits": [],
                        "dfpn_parse_unknown_count": 0,
                        "dfpn_distance_available_count": 0,
                        "dfpn_dialect_used": "none",
                        "dfpn_dialect_candidates": [],
                        "dfpn_source_detail_normalized": "none",
                        "dfpn_pack_source": "builtin",
                        "dfpn_pack_version": "unknown",
                        "dfpn_pack_load_errors": 0,
                        "verify_conflict_count": 0,
                        "verify_unknown_count": 0,
                        "dfpn_parser_mode": "AUTO",
                        "verify_hybrid_policy": "CONSERVATIVE",
                        "hybrid_learned_adjustment_used": False,
                        "hybrid_adjustment_delta": 0.0,
                        "hybrid_adjustment_source": "none",
                        "pseudo_hisshi_status": "not_used",
                        "ponder_status_summary": "not_used",
                        "ponder_cache_used": False,
                        "ponder_cache_hit": False,
                        "ponder_used_budget_ms": 0,
                        "ponder_fallback_reason": None,
                        "ponder_reuse_score": None,
                        "ponder_cache_age_ms": 0,
                        "ponder_cache_gate_reason": None,
                        "ponder_gate_learned_adjustment_used": False,
                        "ponder_gate_adjustment_delta": 0.0,
                        "ponder_gate_adjustment_source": "none",
                        "reuse_then_bestmove_changed": False,
                        "ponder_reuse_decision_id": None,
                        "ponder_reuse_parent_position_key": None,
                        "ponder_label_source": "heuristic",
                        "ponder_label_confidence": 0.0,
                        "backend_restart_count": 0,
                        "dfpn_status": "not_used",
                        "actual_opponent_move": actual_reply,
                        "actual_move_in_reply_topk": None,
                        "actual_move_rank_in_reply_topk": None,
                        "outcome_tag": outcome_tag,
                        "outcome_confidence": outcome_conf,
                        "outcome_match_source": "external_direct",
                        "outcome_match_confidence": 1.0,
                        "outcome_match_candidates": 1,
                        "dry_run": False,
                        "search_id": idx + 1,
                    }
                    dst.write(json.dumps(row, ensure_ascii=False) + "\n")
                    emitted += 1

    summary = {
        "input": str(in_path),
        "output": str(out_path),
        "min_dan": int(args.min_dan),
        "max_dan": int(args.max_dan),
        "exclude_users": sorted(excludes),
        "total_games": total_games,
        "used_games": used_games,
        "emitted_records": emitted,
        "target_users": len(target_users),
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
