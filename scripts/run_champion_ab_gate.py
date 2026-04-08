#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v or v in {float("inf"), float("-inf")}:
        return default
    return v


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_cmd(cmd: list[str], cwd: Path) -> int:
    print(f"[champion] run: {' '.join(shlex.quote(x) for x in cmd)}")
    done = subprocess.run(cmd, cwd=str(cwd), text=True)  # noqa: S603,S607
    return int(done.returncode)


def _find_wrapper_log(run_dir: Path) -> Optional[Path]:
    logs = sorted((run_dir / "wrapper_logs").glob("taso-swindle-*.jsonl"))
    return logs[-1] if logs else None


def _wrapper_lost(game_rec: dict[str, Any]) -> bool:
    winner = str(game_rec.get("winner", "")).strip().lower()
    if winner == "draw" or not winner:
        return False
    wrapper_is_black = bool(game_rec.get("wrapper_is_black"))
    if winner == "black":
        return not wrapper_is_black
    if winner == "white":
        return wrapper_is_black
    return False


@dataclass(frozen=True)
class RunMetrics:
    run_id: str
    records: int
    games: int
    wrapper_wins: int
    wrapper_losses: int
    wrapper_draws: int
    wrapper_win_rate: float
    actual_move_topk_rate: float
    avg_actual_move_rank: float
    verify_timeout_events: int
    verify_error_events: int


def _collect_metrics(run_dir: Path, run_id: str) -> RunMetrics:
    wrapper_log = _find_wrapper_log(run_dir)
    recs: list[dict[str, Any]] = []
    if wrapper_log is not None and wrapper_log.exists():
        for raw in wrapper_log.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                recs.append(rec)

    total = len(recs)
    topk = sum(1 for r in recs if bool(r.get("actual_move_in_reply_topk", False)))
    ranks = [
        int(r.get("actual_move_rank_in_reply_topk"))
        for r in recs
        if isinstance(r.get("actual_move_rank_in_reply_topk"), int)
        and int(r.get("actual_move_rank_in_reply_topk")) > 0
    ]
    verify_timeout = 0
    verify_error = 0
    for r in recs:
        for ev in (r.get("events") or []):
            s = str(ev).lower()
            if "verify" in s and "timeout" in s:
                verify_timeout += 1
            if "verify_error" in s:
                verify_error += 1

    games_path = run_dir / "games.jsonl"
    wins = 0
    losses = 0
    draws = 0
    games = 0
    if games_path.exists():
        for raw in games_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            games += 1
            if str(rec.get("winner", "")).strip().lower() == "draw":
                draws += 1
            elif _wrapper_lost(rec):
                losses += 1
            else:
                wins += 1

    win_rate = (wins / float(games)) if games > 0 else 0.0
    topk_rate = (topk / float(total)) if total > 0 else 0.0
    avg_rank = (sum(ranks) / float(len(ranks))) if ranks else 0.0
    return RunMetrics(
        run_id=run_id,
        records=total,
        games=games,
        wrapper_wins=wins,
        wrapper_losses=losses,
        wrapper_draws=draws,
        wrapper_win_rate=win_rate,
        actual_move_topk_rate=topk_rate,
        avg_actual_move_rank=avg_rank,
        verify_timeout_events=verify_timeout,
        verify_error_events=verify_error,
    )


def _to_dict(m: RunMetrics) -> dict[str, Any]:
    return {
        "run_id": m.run_id,
        "records": m.records,
        "games": m.games,
        "wrapper_wins": m.wrapper_wins,
        "wrapper_losses": m.wrapper_losses,
        "wrapper_draws": m.wrapper_draws,
        "wrapper_win_rate": m.wrapper_win_rate,
        "actual_move_topk_rate": m.actual_move_topk_rate,
        "avg_actual_move_rank": m.avg_actual_move_rank,
        "verify_timeout_events": m.verify_timeout_events,
        "verify_error_events": m.verify_error_events,
    }


def _wrapper_options_for_eval(hybrid_path: Path) -> str:
    items = [
        f"SwindleHybridWeightsPath={hybrid_path}",
        "SwindleUseHybridLearnedAdjustment=true",
        "SwindleUsePonderGateLearnedAdjustment=false",
        "SwindleVerboseInfo=false",
        "SwindleEmitInfoStringLevel=0",
    ]
    return ";".join(items)


def _extract_failure_openings_from_games(
    *,
    games_jsonl: Path,
    out_path: Path,
    max_items: int = 5000,
) -> tuple[int, int]:
    if not games_jsonl.exists():
        _json_dump(out_path.with_suffix(".meta.json"), {"status": "games_missing", "source": str(games_jsonl)})
        return 0, 0
    seen: set[str] = set()
    total = 0
    for raw in games_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        total += 1
        if not _wrapper_lost(rec):
            continue
        opening = rec.get("opening_moves")
        if not isinstance(opening, list):
            continue
        moves = [str(x).strip() for x in opening if str(x).strip()]
        if not moves:
            continue
        seen.add(" ".join(moves))
        if len(seen) >= int(max_items):
            break
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(sorted(seen)) + ("\n" if seen else ""), encoding="utf-8")
    return total, len(seen)


def _summary_md(
    *,
    report: dict[str, Any],
) -> str:
    old_m = report.get("old_metrics", {})
    cand_m = report.get("candidate_metrics", {})
    delta = report.get("delta", {})
    gate = report.get("gate", {})
    lines: list[str] = []
    lines.append(f"# Champion A/B Report: {report.get('run_id')}")
    lines.append("")
    lines.append(f"- Winner: **{report.get('winner')}**")
    lines.append(f"- Adopted: **{bool(report.get('adopted', False))}**")
    lines.append("")
    lines.append("## Delta (candidate - current)")
    lines.append(f"- wrapper_win_rate: {float(delta.get('wrapper_win_rate', 0.0)):+.4f}")
    lines.append(f"- actual_move_topk_rate: {float(delta.get('actual_move_topk_rate', 0.0)):+.4f}")
    lines.append(f"- verify_error_events: {float(delta.get('verify_error_events', 0.0)):+.0f}")
    lines.append("")
    lines.append("## Current")
    lines.append(f"- games={int(old_m.get('games', 0))} wins={int(old_m.get('wrapper_wins', 0))} losses={int(old_m.get('wrapper_losses', 0))} draws={int(old_m.get('wrapper_draws', 0))}")
    lines.append(f"- win_rate={float(old_m.get('wrapper_win_rate', 0.0)):.4f}, topk={float(old_m.get('actual_move_topk_rate', 0.0)):.4f}, verify_error={int(old_m.get('verify_error_events', 0))}")
    lines.append("")
    lines.append("## Candidate")
    lines.append(f"- games={int(cand_m.get('games', 0))} wins={int(cand_m.get('wrapper_wins', 0))} losses={int(cand_m.get('wrapper_losses', 0))} draws={int(cand_m.get('wrapper_draws', 0))}")
    lines.append(f"- win_rate={float(cand_m.get('wrapper_win_rate', 0.0)):.4f}, topk={float(cand_m.get('actual_move_topk_rate', 0.0)):.4f}, verify_error={int(cand_m.get('verify_error_events', 0))}")
    lines.append("")
    lines.append("## Gate")
    lines.append(f"- min_winrate_delta={float(gate.get('min_winrate_delta', 0.0)):+.4f}")
    lines.append(f"- min_topk_delta={float(gate.get('min_topk_delta', 0.0)):+.4f}")
    lines.append(f"- max_verify_error_delta={int(gate.get('max_verify_error_delta', 0)):+d}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Champion A/B gate for hybrid weights using fixed selfplay benchmark.")
    parser.add_argument("--current-hybrid", default="models/hybrid_weights.json")
    parser.add_argument("--candidate-hybrid", required=True)
    parser.add_argument("--output-root", default="artifacts_local/champion_ab")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--report-path", default="")
    parser.add_argument("--summary-path", default="")
    parser.add_argument("--games", type=int, default=700)
    parser.add_argument("--nodes", type=int, default=800)
    parser.add_argument("--max-plies", type=int, default=140)
    parser.add_argument("--opening-file", default="artifacts_local/benchmarks/fixed_bench_openings_v1.txt")
    parser.add_argument("--opening-plies", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260227)
    parser.add_argument("--gate-min-winrate-delta", type=float, default=0.02)
    parser.add_argument("--gate-min-topk-delta", type=float, default=-0.01)
    parser.add_argument("--gate-max-verify-error-delta", type=int, default=1)
    parser.add_argument("--adopt", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--selfplay-script", default="scripts/run_selfplay_dataset.py")
    parser.add_argument("--wrapper-cmd", default="python3 -m taso_swindle.main")
    parser.add_argument("--backend-engine", default="./YaneuraOu")
    parser.add_argument("--backend-eval", default="./eval")
    parser.add_argument("--backend-args", default="")
    parser.add_argument("--backend-options", default="")
    parser.add_argument("--failure-openings-max", type=int, default=5000)
    parser.add_argument("--models-hybrid-path", default="models/hybrid_weights.json")
    parser.add_argument("--snapshot-dir", default="models/snapshots")
    parser.add_argument("--selfplay-think-timeout-sec", type=float, default=0.0)
    parser.add_argument("--selfplay-game-walltime-sec", type=float, default=0.0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = (root / output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    run_id = args.run_id.strip() or f"champion-ab-{_ts()}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    current_hybrid = Path(args.current_hybrid).expanduser()
    if not current_hybrid.is_absolute():
        current_hybrid = (root / current_hybrid).resolve()
    candidate_hybrid = Path(args.candidate_hybrid).expanduser()
    if not candidate_hybrid.is_absolute():
        candidate_hybrid = (root / candidate_hybrid).resolve()
    opening_file = Path(args.opening_file).expanduser()
    if not opening_file.is_absolute():
        opening_file = (root / opening_file).resolve()

    if not current_hybrid.exists():
        raise SystemExit(f"missing current hybrid: {current_hybrid}")
    if not candidate_hybrid.exists():
        raise SystemExit(f"missing candidate hybrid: {candidate_hybrid}")
    if not opening_file.exists():
        raise SystemExit(f"missing opening file: {opening_file}")

    selfplay_script = Path(args.selfplay_script).expanduser()
    if not selfplay_script.is_absolute():
        selfplay_script = (root / selfplay_script).resolve()
    if not selfplay_script.exists():
        raise SystemExit(f"missing selfplay script: {selfplay_script}")

    bench_root = run_dir / "selfplay_ab"
    old_opts = _wrapper_options_for_eval(current_hybrid)
    cand_opts = _wrapper_options_for_eval(candidate_hybrid)

    common = [
        "python3",
        str(selfplay_script),
        "--games",
        str(int(args.games)),
        "--nodes",
        str(int(args.nodes)),
        "--max-plies",
        str(int(args.max_plies)),
        "--seed",
        str(int(args.seed)),
        "--opening-file",
        str(opening_file),
        "--opening-plies",
        str(int(args.opening_plies)),
        "--output-root",
        str(bench_root),
        "--no-auto-pipeline",
        "--wrapper-cmd",
        args.wrapper_cmd,
        "--backend-engine",
        args.backend_engine,
        "--backend-eval",
        args.backend_eval,
        "--backend-args",
        args.backend_args,
        "--backend-options",
        args.backend_options,
    ]
    if float(args.selfplay_think_timeout_sec) > 0:
        common += ["--think-timeout-sec", f"{float(args.selfplay_think_timeout_sec):.3f}"]
    if float(args.selfplay_game_walltime_sec) > 0:
        common += ["--game-walltime-sec", f"{float(args.selfplay_game_walltime_sec):.3f}"]
    old_cmd = common + ["--run-id", "ab_old", "--wrapper-options", old_opts]
    cand_cmd = common + ["--run-id", "ab_cand", "--wrapper-options", cand_opts]

    report_path = Path(args.report_path).expanduser() if args.report_path.strip() else (run_dir / "reports" / "champion_ab_report.json")
    if not report_path.is_absolute():
        report_path = (root / report_path).resolve()
    summary_path = Path(args.summary_path).expanduser() if args.summary_path.strip() else (run_dir / "reports" / "champion_ab_summary.md")
    if not summary_path.is_absolute():
        summary_path = (root / summary_path).resolve()

    old_rc = _run_cmd(old_cmd, root)
    cand_rc = _run_cmd(cand_cmd, root)
    if old_rc != 0 or cand_rc != 0:
        fail_stage = "ab_old" if old_rc != 0 else "ab_cand"
        fail_report = {
            "run_id": run_id,
            "timestamp": _now_iso(),
            "status": "failed",
            "failed_stage": fail_stage,
            "old_rc": int(old_rc),
            "cand_rc": int(cand_rc),
            "current_hybrid_path": str(current_hybrid),
            "candidate_hybrid_path": str(candidate_hybrid),
            "benchmark": {
                "games": int(args.games),
                "nodes": int(args.nodes),
                "max_plies": int(args.max_plies),
                "opening_file": str(opening_file),
                "opening_plies": int(args.opening_plies),
                "seed": int(args.seed),
            },
            "ab_old_summary_path": str(bench_root / "ab_old" / "summary.json"),
            "ab_cand_summary_path": str(bench_root / "ab_cand" / "summary.json"),
        }
        _json_dump(report_path, fail_report)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            "# Champion A/B Report: failed\n\n"
            f"- failed_stage: `{fail_stage}`\n"
            f"- old_rc: `{old_rc}`\n"
            f"- cand_rc: `{cand_rc}`\n",
            encoding="utf-8",
        )
        print(f"REPORT_PATH={report_path}")
        print(f"SUMMARY_PATH={summary_path}")
        raise SystemExit(f"selfplay failed: old_rc={old_rc}, cand_rc={cand_rc}")

    m_old = _collect_metrics(bench_root / "ab_old", "ab_old")
    m_cand = _collect_metrics(bench_root / "ab_cand", "ab_cand")
    delta = {
        "wrapper_win_rate": m_cand.wrapper_win_rate - m_old.wrapper_win_rate,
        "actual_move_topk_rate": m_cand.actual_move_topk_rate - m_old.actual_move_topk_rate,
        "verify_error_events": m_cand.verify_error_events - m_old.verify_error_events,
    }
    gate = {
        "min_winrate_delta": float(args.gate_min_winrate_delta),
        "min_topk_delta": float(args.gate_min_topk_delta),
        "max_verify_error_delta": int(args.gate_max_verify_error_delta),
    }
    candidate_pass = (
        delta["wrapper_win_rate"] >= gate["min_winrate_delta"]
        and delta["actual_move_topk_rate"] >= gate["min_topk_delta"]
        and delta["verify_error_events"] <= gate["max_verify_error_delta"]
    )
    winner = "candidate" if candidate_pass else "current"
    adopted = bool(args.adopt and candidate_pass)

    models_hybrid = Path(args.models_hybrid_path).expanduser()
    if not models_hybrid.is_absolute():
        models_hybrid = (root / models_hybrid).resolve()
    before_hash = _file_sha256(models_hybrid) if models_hybrid.exists() else ""
    if adopted:
        models_hybrid.parent.mkdir(parents=True, exist_ok=True)
        try:
            same_target = candidate_hybrid.resolve() == models_hybrid.resolve()
        except Exception:
            same_target = False
        if not same_target:
            shutil.copy2(candidate_hybrid, models_hybrid)
    after_hash = _file_sha256(models_hybrid) if models_hybrid.exists() else ""

    snapshots_dir = Path(args.snapshot_dir).expanduser()
    if not snapshots_dir.is_absolute():
        snapshots_dir = (root / snapshots_dir).resolve()
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_name = f"hybrid_weights_{_ts()}_{winner}.json"
    snapshot_path = snapshots_dir / snapshot_name
    source_for_snapshot = candidate_hybrid if winner == "candidate" else current_hybrid
    shutil.copy2(source_for_snapshot, snapshot_path)

    failure_openings_path = run_dir / "failure_band" / f"failure_openings_{_ts()}.txt"
    loss_total, loss_opening_count = _extract_failure_openings_from_games(
        games_jsonl=bench_root / "ab_cand" / "games.jsonl",
        out_path=failure_openings_path,
        max_items=int(args.failure_openings_max),
    )

    report = {
        "run_id": run_id,
        "timestamp": _now_iso(),
        "status": "ok",
        "failed_stage": "",
        "current_hybrid_path": str(current_hybrid),
        "candidate_hybrid_path": str(candidate_hybrid),
        "current_hybrid_sha256": _file_sha256(current_hybrid),
        "candidate_hybrid_sha256": _file_sha256(candidate_hybrid),
        "models_hybrid_path": str(models_hybrid),
        "models_hybrid_sha256_before": before_hash,
        "models_hybrid_sha256_after": after_hash,
        "benchmark": {
            "games": int(args.games),
            "nodes": int(args.nodes),
            "max_plies": int(args.max_plies),
            "opening_file": str(opening_file),
            "opening_plies": int(args.opening_plies),
            "seed": int(args.seed),
        },
        "old_metrics": _to_dict(m_old),
        "candidate_metrics": _to_dict(m_cand),
        "delta": delta,
        "gate": gate,
        "candidate_pass": bool(candidate_pass),
        "winner": winner,
        "adopt_requested": bool(args.adopt),
        "adopted": bool(adopted),
        "adoption_reason": "candidate_pass_gate" if adopted else ("gate_not_passed" if not candidate_pass else "adopt_disabled"),
        "snapshot_path": str(snapshot_path),
        "ab_old_summary_path": str(bench_root / "ab_old" / "summary.json"),
        "ab_cand_summary_path": str(bench_root / "ab_cand" / "summary.json"),
        "failure_openings_path": str(failure_openings_path),
        "failure_openings_total_games": int(loss_total),
        "failure_openings_count": int(loss_opening_count),
    }

    _json_dump(report_path, report)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(_summary_md(report=report), encoding="utf-8")
    print(f"REPORT_PATH={report_path}")
    print(f"SUMMARY_PATH={summary_path}")
    print(f"SNAPSHOT_PATH={snapshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
