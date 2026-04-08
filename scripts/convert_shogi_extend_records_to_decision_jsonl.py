#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from game_id_normalizer import detect_game_id_source, normalize_game_id


@dataclass(frozen=True)
class GameMeta:
    key: str
    timestamp: str
    target_side: str
    side_judges: dict[str, str]
    position_prefix: str
    moves: list[str]


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


def _extract_game_meta(rec: dict[str, Any], user_key: str) -> Optional[GameMeta]:
    key = str(rec.get("key", "")).strip()
    if not key:
        return None

    side_judges: dict[str, str] = {}
    target_side = ""

    memberships = rec.get("memberships")
    if isinstance(memberships, list):
        for row in memberships:
            if not isinstance(row, dict):
                continue
            side = str(row.get("location_key", "")).strip().lower()
            judge = str(row.get("judge_key", "")).strip().lower()
            user = row.get("user")
            row_user_key = ""
            if isinstance(user, dict):
                row_user_key = str(user.get("key", "")).strip()
            if side in {"black", "white"} and judge:
                side_judges[side] = judge
            if row_user_key == user_key and side in {"black", "white"}:
                target_side = side

    if not target_side:
        return None

    position_prefix, moves = _parse_position_and_moves(str(rec.get("sfen_body", "")))
    if not moves:
        return None

    timestamp = str(rec.get("battled_at", "")).strip()
    if not timestamp:
        timestamp = str(rec.get("timestamp", "")).strip()

    return GameMeta(
        key=key,
        timestamp=timestamp,
        target_side=target_side,
        side_judges=side_judges,
        position_prefix=position_prefix,
        moves=moves,
    )


def _to_outcome_tag(judge: str) -> tuple[str, float]:
    j = (judge or "").strip().lower()
    if j == "win":
        return "win", 0.95
    if j == "lose":
        return "loss", 0.95
    if j in {"draw", "dchikiwake", "jishogi"}:
        return "draw", 0.85
    return "unknown", 0.2


def _root_position_cmd(prefix: str, moves: list[str], idx: int) -> str:
    if idx <= 0:
        return prefix
    return f"{prefix} moves {' '.join(moves[:idx])}"


def _candidate_side(idx: int) -> str:
    return "black" if (idx % 2 == 0) else "white"


def _game_id_fields(game_id: str) -> tuple[str, str]:
    source = detect_game_id_source(game_id)
    normalized = normalize_game_id(game_id, source)
    return source, normalized


def _iter_events(meta: GameMeta, user_key: str, target_only: bool) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    game_source, game_normalized = _game_id_fields(meta.key)

    for idx, move in enumerate(meta.moves):
        side = _candidate_side(idx)
        if target_only and side != meta.target_side:
            continue

        actual_reply = meta.moves[idx + 1] if (idx + 1) < len(meta.moves) else None
        judge = meta.side_judges.get(side, "")
        outcome_tag, outcome_conf = _to_outcome_tag(judge)

        row: dict[str, Any] = {
            "timestamp": meta.timestamp,
            "game_id": meta.key,
            "game_id_raw": meta.key,
            "game_id_source_detected": game_source,
            "game_id_normalized": game_normalized,
            "ply": idx + 1,
            "root_sfen": _root_position_cmd(meta.position_prefix, meta.moves, idx),
            "root_eval_cp": None,
            "root_mate": None,
            "swindle_enabled": True,
            "mode": "HYBRID",
            "normal_bestmove": move,
            "final_bestmove": move,
            "candidates": [],
            "selected_reason": "external_human_replay",
            "backend_engine_info": {"name": "external_kifu", "author": user_key},
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
        events.append(row)
    return events


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert shogi-extend extracted records.jsonl into DecisionEvent-compatible JSONL."
    )
    parser.add_argument("--input", required=True, help="path to records.jsonl from extract_shogi_extend_user_kifu.py")
    parser.add_argument("--user-key", required=True, help="target user key, e.g. K_Yamawasabi")
    parser.add_argument("--output", required=True, help="output DecisionEvent-like JSONL path")
    parser.add_argument(
        "--target-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="emit only target user's move plies",
    )
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    total_games = 0
    used_games = 0
    emitted = 0
    skipped_missing_user = 0
    skipped_no_moves = 0

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

            meta = _extract_game_meta(rec, args.user_key)
            if meta is None:
                memberships = rec.get("memberships")
                if isinstance(memberships, list):
                    skipped_missing_user += 1
                else:
                    skipped_no_moves += 1
                continue

            events = _iter_events(meta, args.user_key, bool(args.target_only))
            if not events:
                skipped_no_moves += 1
                continue
            used_games += 1
            for event in events:
                dst.write(json.dumps(event, ensure_ascii=False) + "\n")
                emitted += 1

    summary = {
        "input": str(in_path),
        "output": str(out_path),
        "user_key": args.user_key,
        "target_only": bool(args.target_only),
        "total_games": total_games,
        "used_games": used_games,
        "emitted_records": emitted,
        "skipped_missing_user": skipped_missing_user,
        "skipped_no_moves": skipped_no_moves,
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
