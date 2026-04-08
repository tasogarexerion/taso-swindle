#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from game_id_normalizer import detect_game_id_source, normalize_game_id

USI_MOVE_RE = re.compile(r"^(?:[1-9][a-i][1-9][a-i]\+?|[PLNSGBRK]\*[1-9][a-i])$")
KIF_LINE_RE = re.compile(r"^\s*\d+\s+([^\s]+)")
KIF_META_RE = re.compile(r"^([^：:]+)[：:](.+)$")


@dataclass(frozen=True)
class KifGame:
    path: Path
    moves: list[str]
    game_id: str
    game_id_normalized: str
    game_id_source: str
    start_time: str
    sente: str
    gote: str


@dataclass(frozen=True)
class MatchResult:
    game: Optional[KifGame]
    source: str
    confidence: float
    candidates: int


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _clamp01(value: float) -> float:
    if value != value or value == float("inf") or value == float("-inf"):
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _collect_kif_paths(kif: str, kif_dir: str) -> list[Path]:
    paths: list[Path] = []
    raw_file = (kif or "").strip()
    if raw_file:
        p = Path(raw_file)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.kif")))
            paths.extend(sorted(p.glob("*.KIF")))
        elif p.exists():
            paths.append(p)

    raw_dir = (kif_dir or "").strip()
    if raw_dir:
        d = Path(raw_dir)
        if d.exists() and d.is_dir():
            paths.extend(sorted(d.glob("*.kif")))
            paths.extend(sorted(d.glob("*.KIF")))

    dedup: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        dedup.append(path)
    return dedup


def parse_kif(path: Path) -> KifGame:
    moves: list[str] = []
    game_id = ""
    start_time = ""
    sente = ""
    gote = ""

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("*"):
            continue

        meta = KIF_META_RE.match(line)
        if meta:
            key = _norm(meta.group(1))
            val = str(meta.group(2)).strip()
            if key in {"対局id", "gameid", "game_id"} and not game_id:
                game_id = val
            elif key in {"開始日時", "開始日", "対局日時", "starttime", "date"} and not start_time:
                start_time = val
            elif key in {"先手", "sente", "black"} and not sente:
                sente = val
            elif key in {"後手", "gote", "white"} and not gote:
                gote = val

        m = KIF_LINE_RE.match(line)
        if not m:
            continue
        token = m.group(1).strip()
        if USI_MOVE_RE.match(token):
            moves.append(token)

    return KifGame(
        path=path,
        moves=moves,
        game_id=game_id,
        game_id_normalized=normalize_game_id(game_id or path.stem, None),
        game_id_source=detect_game_id_source(game_id or str(path)),
        start_time=start_time,
        sente=sente,
        gote=gote,
    )


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


def _rank_in_topk(reply_topk: list[dict[str, Any]], move: str) -> Optional[int]:
    for idx, item in enumerate(reply_topk):
        m = str(item.get("move", "")) if isinstance(item, dict) else ""
        if m == move:
            return idx + 1
    return None


def _infer_outcome_tag(
    *,
    actual_move: Optional[str],
    rank: Optional[int],
    in_topk: Optional[bool],
    root_eval_cp: Optional[int],
    selected_base_cp: Optional[int],
) -> tuple[str, float]:
    if not actual_move:
        return "unknown", 0.2

    improve: Optional[int] = None
    if isinstance(root_eval_cp, int) and isinstance(selected_base_cp, int):
        improve = selected_base_cp - root_eval_cp

    if in_topk is False:
        if improve is not None and improve >= 120:
            return "swing_success", 0.78
        return "neutral", 0.46

    if in_topk is True and rank == 1:
        if improve is None or improve < 120:
            return "swing_fail", 0.74
        return "neutral", 0.52

    if in_topk is True and isinstance(rank, int) and rank >= 3:
        if improve is None or improve >= 60:
            return "swing_success", 0.64
        return "neutral", 0.50

    if in_topk is True:
        return "neutral", 0.50
    return "unknown", 0.25


def _match_actual_move(rec: dict[str, Any], game_moves: list[str]) -> Optional[str]:
    ply = _safe_int(rec.get("ply"))
    if ply is None:
        return None

    final = str(rec.get("final_bestmove", ""))
    if not USI_MOVE_RE.match(final):
        return None

    if 0 <= ply < len(game_moves) and game_moves[ply] == final:
        if ply + 1 < len(game_moves):
            return game_moves[ply + 1]
        return None

    for idx in range(max(0, ply - 2), min(len(game_moves), ply + 3)):
        if game_moves[idx] == final and idx + 1 < len(game_moves):
            return game_moves[idx + 1]
    return None


def _score_game_match(rec: dict[str, Any], game: KifGame) -> float:
    score = 0.0
    rec_gid = str(rec.get("game_id", "")).strip()
    rec_gid_norm = normalize_game_id(rec_gid, detect_game_id_source(rec_gid))
    stem = game.path.stem
    stem_norm = normalize_game_id(stem, game.game_id_source)

    if rec_gid:
        stem = game.path.stem
        if rec_gid == game.game_id or rec_gid == stem:
            score += 0.75
        elif rec_gid in stem or (game.game_id and rec_gid in game.game_id):
            score += 0.45
    if rec_gid_norm and (rec_gid_norm == game.game_id_normalized or rec_gid_norm == stem_norm):
        score += 0.70

    ply = _safe_int(rec.get("ply"))
    final = str(rec.get("final_bestmove", "")).strip()
    if ply is not None and USI_MOVE_RE.match(final):
        if 0 <= ply < len(game.moves) and game.moves[ply] == final:
            score += 0.50
        elif any(
            game.moves[idx] == final
            for idx in range(max(0, ply - 2), min(len(game.moves), ply + 3))
        ):
            score += 0.18

    if game.start_time and str(rec.get("timestamp", "")).strip():
        ts = str(rec.get("timestamp", ""))
        if game.start_time[:10] and game.start_time[:10] in ts:
            score += 0.10

    return _clamp01(score)


def _pick_best_game(rec: dict[str, Any], games: list[KifGame]) -> MatchResult:
    if not games:
        return MatchResult(game=None, source="unmatched", confidence=0.0, candidates=0)

    scored: list[tuple[float, KifGame]] = []
    for game in games:
        score = _score_game_match(rec, game)
        if score > 0.0:
            scored.append((score, game))

    if not scored:
        return MatchResult(game=None, source="unmatched", confidence=0.0, candidates=0)

    scored.sort(key=lambda x: x[0], reverse=True)
    top_score, top_game = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    candidates = len(scored)

    if len(scored) > 1 and (top_score - second_score) < 0.08 and top_score < 0.90:
        return MatchResult(game=None, source="unmatched", confidence=0.25, candidates=candidates)

    rec_gid = str(rec.get("game_id", "")).strip()
    rec_gid_norm = normalize_game_id(rec_gid, detect_game_id_source(rec_gid))
    top_stem_norm = normalize_game_id(top_game.path.stem, top_game.game_id_source)
    if rec_gid and (rec_gid == top_game.game_id or rec_gid == top_game.path.stem):
        source = "game_id_exact"
        confidence = max(0.92, top_score)
    elif rec_gid_norm and (rec_gid_norm == top_game.game_id_normalized or rec_gid_norm == top_stem_norm):
        source = "game_id_normalized_exact"
        confidence = max(0.88, top_score)
    elif top_score >= 0.70:
        source = "meta_strong"
        confidence = max(0.70, top_score)
    elif top_score >= 0.45:
        source = "meta_weak"
        confidence = max(0.45, top_score)
    else:
        return MatchResult(game=None, source="unmatched", confidence=top_score, candidates=candidates)

    confidence = _clamp01(confidence - (0.15 if candidates > 1 and second_score > 0.0 else 0.0))
    if confidence < 0.45:
        return MatchResult(game=None, source="unmatched", confidence=confidence, candidates=candidates)
    return MatchResult(game=top_game, source=source, confidence=confidence, candidates=candidates)


def fill_records(records: list[dict[str, Any]], games: list[KifGame]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in records:
        row = dict(rec)
        match = _pick_best_game(row, games)

        actual_move = _match_actual_move(row, match.game.moves) if match.game is not None else None

        selected = _extract_selected_candidate(row)
        reply_topk: list[dict[str, Any]] = []
        selected_base_cp: Optional[int] = None
        if isinstance(selected, dict):
            raw_topk = selected.get("reply_topk")
            if isinstance(raw_topk, list):
                reply_topk = [x for x in raw_topk if isinstance(x, dict)]
            try:
                selected_base_cp = int(selected.get("base_cp")) if selected.get("base_cp") is not None else None
            except Exception:
                selected_base_cp = None

        rank = _rank_in_topk(reply_topk, actual_move) if actual_move else None
        in_topk = (rank is not None) if actual_move else None
        root_eval_cp = row.get("root_eval_cp")
        if not isinstance(root_eval_cp, int):
            root_eval_cp = None

        tag, conf = _infer_outcome_tag(
            actual_move=actual_move,
            rank=rank,
            in_topk=in_topk,
            root_eval_cp=root_eval_cp,
            selected_base_cp=selected_base_cp,
        )

        row["actual_opponent_move"] = actual_move
        row["actual_move_in_reply_topk"] = in_topk
        row["actual_move_rank_in_reply_topk"] = rank
        row["outcome_tag"] = tag
        row["outcome_confidence"] = conf
        row["outcome_match_source"] = match.source
        row["outcome_match_confidence"] = _clamp01(match.confidence)
        row["outcome_match_candidates"] = int(match.candidates)
        rec_gid = str(row.get("game_id", "")).strip()
        rec_src = detect_game_id_source(rec_gid)
        row["game_id_raw"] = rec_gid
        row["game_id_normalized"] = normalize_game_id(rec_gid, rec_src)
        row["game_id_source_detected"] = rec_src
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill DecisionEvent JSONL with actual_opponent_move/outcome_tag from KIF")
    parser.add_argument("--input", required=True, help="input DecisionEvent JSONL")
    parser.add_argument("--kif", default="", help="kif file path (or directory)")
    parser.add_argument("--kif-dir", default="", help="kif directory (multiple files scanned)")
    parser.add_argument("--output", required=True, help="output labeled JSONL path")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)

    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    kif_paths = _collect_kif_paths(args.kif, args.kif_dir)
    if not kif_paths:
        raise SystemExit("no kif files found: pass --kif or --kif-dir")

    games = [parse_kif(path) for path in kif_paths]

    records: list[dict[str, Any]] = []
    with in_path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                records.append(rec)

    labeled = fill_records(records, games)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in labeled:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"wrote {out_path} records={len(labeled)} games={len(games)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
