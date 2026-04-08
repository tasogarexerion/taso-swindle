#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _json_load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _latest_openings(path: Path) -> Optional[Path]:
    candidates = sorted(path.glob("failure_openings_*.txt"), key=lambda p: p.name, reverse=True)
    return candidates[0] if candidates else None


def _ensure_fixed_openings(fixed_path: Path, failure_openings_dir: Path) -> Path:
    if fixed_path.exists():
        return fixed_path
    src = _latest_openings(failure_openings_dir)
    if src is None:
        raise SystemExit(f"no failure openings found in: {failure_openings_dir}")
    fixed_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, fixed_path)
    return fixed_path


def _run_cmd(
    cmd: list[str],
    cwd: Path,
    *,
    timeout_sec: int = 0,
    stop_file: Optional[Path] = None,
    heartbeat: Optional[Callable[[str], None]] = None,
    heartbeat_stage: str = "",
    heartbeat_interval_sec: int = 10,
) -> int:
    print(f"[autopilot] run: {' '.join(shlex.quote(x) for x in cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(cwd), text=True)  # noqa: S603,S607
    started = time.time()
    last_hb = 0.0
    while True:
        rc = proc.poll()
        now = time.time()
        if rc is not None:
            return int(rc)
        if stop_file is not None and stop_file.exists():
            print(f"[autopilot] stop requested during stage={heartbeat_stage}")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return 130
        if int(timeout_sec) > 0 and (now - started) > int(timeout_sec):
            print(f"[autopilot] timeout stage={heartbeat_stage} timeout_sec={timeout_sec}")
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return 124
        if heartbeat is not None and (now - last_hb) >= max(2, int(heartbeat_interval_sec)):
            try:
                heartbeat(heartbeat_stage)
            except Exception:
                pass
            last_hb = now
        time.sleep(0.5)


def _find_wrapper_log(run_dir: Path) -> Optional[Path]:
    logs = sorted((run_dir / "wrapper_logs").glob("taso-swindle-*.jsonl"))
    return logs[-1] if logs else None


def _latest_learning_run(learning_root: Path) -> Optional[Path]:
    if not learning_root.exists():
        return None
    runs = sorted([p for p in learning_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    return runs[-1] if runs else None


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


def _wrapper_options_for_eval(hybrid_path: Path, ponder_path: Optional[Path], *, verbose: bool = False) -> str:
    items = [
        f"SwindleHybridWeightsPath={hybrid_path}",
        "SwindleUseHybridLearnedAdjustment=true",
    ]
    if ponder_path is not None and ponder_path.exists():
        items.append(f"SwindlePonderGateWeightsPath={ponder_path}")
        items.append("SwindleUsePonderGateLearnedAdjustment=true")
    else:
        items.append("SwindleUsePonderGateLearnedAdjustment=false")
    items.append(f"SwindleVerboseInfo={'true' if verbose else 'false'}")
    items.append("SwindleEmitInfoStringLevel=0")
    return ";".join(items)


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


def _summarize_cycle_markdown(
    *,
    cycle_id: str,
    adopt: bool,
    old_m: RunMetrics,
    cand_m: RunMetrics,
    delta: dict[str, float],
    gate: dict[str, Any],
    next_action: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append(f"# Strength Cycle Summary: {cycle_id}")
    lines.append("")
    lines.append(f"- Result: {'ADOPTED' if adopt else 'REJECTED'}")
    lines.append(f"- Wrapper win-rate delta: {delta.get('wrapper_win_rate', 0.0):+.4f}")
    lines.append(f"- TopK delta: {delta.get('actual_move_topk_rate', 0.0):+.4f}")
    lines.append(f"- Verify error delta: {delta.get('verify_error_events', 0.0):+.0f}")
    lines.append("")
    lines.append("## Old")
    lines.append(f"- games={old_m.games} wins={old_m.wrapper_wins} losses={old_m.wrapper_losses} draws={old_m.wrapper_draws}")
    lines.append(
        f"- win_rate={old_m.wrapper_win_rate:.4f}, topk={old_m.actual_move_topk_rate:.4f}, avg_rank={old_m.avg_actual_move_rank:.4f}, verify_error={old_m.verify_error_events}"
    )
    lines.append("")
    lines.append("## Candidate")
    lines.append(
        f"- games={cand_m.games} wins={cand_m.wrapper_wins} losses={cand_m.wrapper_losses} draws={cand_m.wrapper_draws}"
    )
    lines.append(
        f"- win_rate={cand_m.wrapper_win_rate:.4f}, topk={cand_m.actual_move_topk_rate:.4f}, avg_rank={cand_m.avg_actual_move_rank:.4f}, verify_error={cand_m.verify_error_events}"
    )
    lines.append("")
    lines.append("## Gate")
    lines.append(f"- min_winrate_delta={float(gate.get('min_winrate_delta', 0.0)):+.4f}")
    lines.append(f"- min_topk_delta={float(gate.get('min_topk_delta', 0.0)):+.4f}")
    lines.append(f"- max_verify_error_delta={int(gate.get('max_verify_error_delta', 0)):+d}")
    lines.append("")
    lines.append("## Next Process")
    lines.append(f"- proposer: {next_action.get('proposer', 'n/a')}")
    lines.append(f"- manager: {next_action.get('manager', 'n/a')}")
    lines.append(f"- decision: {next_action.get('decision', 'n/a')}")
    lines.append(f"- auto_executed: {bool(next_action.get('auto_executed', False))}")
    reason = str(next_action.get("reason", "")).strip()
    if reason:
        lines.append(f"- reason: {reason}")
    return "\n".join(lines) + "\n"


def _propose_and_decide_next_action(
    *,
    adopt: bool,
    delta: dict[str, float],
    runtime_params: dict[str, Any],
) -> dict[str, Any]:
    # Proposer-Codex: aggressive improvement idea
    proposer = "continue_default_cycle"
    proposer_reason = "baseline"
    if not adopt and float(delta.get("wrapper_win_rate", 0.0)) < 0.0:
        proposer = "expand_training_games"
        proposer_reason = "candidate lost in win-rate; need denser failure-band samples"
    if not adopt and float(delta.get("actual_move_topk_rate", 0.0)) < -0.02:
        proposer = "raise_label_confidence"
        proposer_reason = "topk degraded; likely noisy labels"

    # Manager-Codex: conservative governance
    manager = proposer
    manager_reason = proposer_reason
    auto_executed = False
    rp = dict(runtime_params)

    if proposer == "expand_training_games":
        before = int(rp.get("train_games", 300))
        after = min(800, before + 100)
        rp["train_games"] = after
        manager_reason = f"train_games {before}->{after}"
        auto_executed = after != before
    elif proposer == "raise_label_confidence":
        before = float(rp.get("min_outcome_confidence", 0.40))
        after = min(0.60, before + 0.05)
        rp["min_outcome_confidence"] = after
        rp["min_outcome_match_confidence"] = after
        manager_reason = f"min_outcome_confidence {before:.2f}->{after:.2f}"
        auto_executed = abs(after - before) > 1e-9
    else:
        manager_reason = "no change"

    return {
        "proposer": proposer,
        "manager": manager,
        "decision": proposer,
        "reason": manager_reason or proposer_reason,
        "auto_executed": auto_executed,
        "runtime_params": rp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Automate strength operations: fixed-bench regression + long A/B + threshold-based adoption."
    )
    parser.add_argument("--output-root", default="artifacts_local/strength_autopilot")
    parser.add_argument("--fixed-openings", default="artifacts_local/benchmarks/fixed_bench_openings_v1.txt")
    parser.add_argument("--failure-openings-dir", default="artifacts_local/failure_band")

    parser.add_argument("--train-games", type=int, default=300)
    parser.add_argument("--ab-games", type=int, default=500)
    parser.add_argument("--nodes", type=int, default=1400)
    parser.add_argument("--max-plies", type=int, default=180)
    parser.add_argument("--opening-plies", type=int, default=12)
    parser.add_argument("--seed", type=int, default=20260227)

    parser.add_argument("--train-hybrid", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--train-ponder", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--min-outcome-confidence", type=float, default=0.40)
    parser.add_argument("--min-outcome-match-confidence", type=float, default=0.40)
    parser.add_argument("--min-ponder-label-confidence", type=float, default=0.00)

    parser.add_argument("--hybrid-model-path", default="models/hybrid_weights.json")
    parser.add_argument("--ponder-model-path", default="models/ponder_gate_weights.json")

    parser.add_argument("--gate-min-winrate-delta", type=float, default=0.02)
    parser.add_argument("--gate-min-topk-delta", type=float, default=-0.01)
    parser.add_argument("--gate-max-verify-error-delta", type=int, default=1)
    parser.add_argument("--train-timeout-sec", type=int, default=7200)
    parser.add_argument("--ab-timeout-sec", type=int, default=7200)
    parser.add_argument("--heartbeat-interval-sec", type=int, default=10)

    parser.add_argument("--interval-sec", type=int, default=21600, help="sleep between cycles (default 6h)")
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means run forever")
    parser.add_argument("--stop-file", default="artifacts_local/strength_autopilot/STOP")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = (root / output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    state_path = output_root / "state.json"
    history_path = output_root / "history.jsonl"
    stop_file = Path(args.stop_file).expanduser()
    if not stop_file.is_absolute():
        stop_file = (root / stop_file).resolve()

    fixed_openings = Path(args.fixed_openings).expanduser()
    if not fixed_openings.is_absolute():
        fixed_openings = (root / fixed_openings).resolve()
    failure_openings_dir = Path(args.failure_openings_dir).expanduser()
    if not failure_openings_dir.is_absolute():
        failure_openings_dir = (root / failure_openings_dir).resolve()
    fixed_openings = _ensure_fixed_openings(fixed_openings, failure_openings_dir)

    hybrid_model = Path(args.hybrid_model_path).expanduser()
    if not hybrid_model.is_absolute():
        hybrid_model = (root / hybrid_model).resolve()
    ponder_model = Path(args.ponder_model_path).expanduser()
    if not ponder_model.is_absolute():
        ponder_model = (root / ponder_model).resolve()

    default_runtime_params = {
        "train_games": int(args.train_games),
        "ab_games": int(args.ab_games),
        "nodes": int(args.nodes),
        "max_plies": int(args.max_plies),
        "opening_plies": int(args.opening_plies),
        "seed": int(args.seed),
        "min_outcome_confidence": float(args.min_outcome_confidence),
        "min_outcome_match_confidence": float(args.min_outcome_match_confidence),
        "min_ponder_label_confidence": float(args.min_ponder_label_confidence),
    }

    state = _json_load(state_path) if state_path.exists() else {}
    if not state:
        state = {
            "started_at": _now_iso(),
            "cycles_done": 0,
            "cycles_adopted": 0,
            "cycles_failed": 0,
            "last_status": "initialized",
            "fixed_openings": str(fixed_openings),
            "gate": {
                "min_winrate_delta": float(args.gate_min_winrate_delta),
                "min_topk_delta": float(args.gate_min_topk_delta),
                "max_verify_error_delta": int(args.gate_max_verify_error_delta),
            },
            "runtime_params": dict(default_runtime_params),
        }
        _json_dump(state_path, state)
    if not isinstance(state.get("runtime_params"), dict) or not dict(state.get("runtime_params") or {}):
        state["runtime_params"] = dict(default_runtime_params)
        state["updated_at"] = _now_iso()
        _json_dump(state_path, state)

    def _heartbeat(stage: str) -> None:
        state["heartbeat_at"] = _now_iso()
        state["current_stage"] = stage
        state["updated_at"] = state["heartbeat_at"]
        _json_dump(state_path, state)

    cycle = int(state.get("cycles_done", 0))
    while True:
        if int(args.max_cycles) > 0 and cycle >= int(args.max_cycles):
            break
        if stop_file.exists():
            print(f"[autopilot] stop file detected: {stop_file}")
            break

        cycle += 1
        cycle_id = f"cycle-{cycle:04d}-{_ts()}"
        cycle_dir = output_root / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)
        print(f"[autopilot] start {cycle_id}")
        state["current_cycle"] = cycle_id
        _heartbeat("cycle_start")

        rp = dict(default_runtime_params)
        rp.update(dict(state.get("runtime_params") or {}))
        train_games = int(rp.get("train_games", int(args.train_games)))
        ab_games = int(rp.get("ab_games", int(args.ab_games)))
        nodes = int(rp.get("nodes", int(args.nodes)))
        max_plies = int(rp.get("max_plies", int(args.max_plies)))
        opening_plies = int(rp.get("opening_plies", int(args.opening_plies)))
        seed = int(rp.get("seed", int(args.seed)))
        min_outcome_confidence = float(rp.get("min_outcome_confidence", float(args.min_outcome_confidence)))
        min_outcome_match_confidence = float(
            rp.get("min_outcome_match_confidence", float(args.min_outcome_match_confidence))
        )
        min_ponder_label_confidence = float(
            rp.get("min_ponder_label_confidence", float(args.min_ponder_label_confidence))
        )

        old_hybrid_snapshot = cycle_dir / "weights" / "old_hybrid_snapshot.json"
        old_hybrid_snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not hybrid_model.exists():
            raise SystemExit(f"missing hybrid model: {hybrid_model}")
        shutil.copy2(hybrid_model, old_hybrid_snapshot)

        old_ponder_snapshot: Optional[Path] = None
        if ponder_model.exists():
            old_ponder_snapshot = cycle_dir / "weights" / "old_ponder_snapshot.json"
            shutil.copy2(ponder_model, old_ponder_snapshot)

        train_root = cycle_dir / "selfplay_train"
        train_run_id = "train"
        train_cmd = [
            "python3",
            "scripts/run_selfplay_dataset.py",
            "--games",
            str(int(train_games)),
            "--nodes",
            str(int(nodes)),
            "--max-plies",
            str(int(max_plies)),
            "--seed",
            str(int(seed)),
            "--opening-file",
            str(fixed_openings),
            "--opening-plies",
            str(int(opening_plies)),
            "--output-root",
            str(train_root),
            "--run-id",
            train_run_id,
            "--min-outcome-confidence",
            f"{float(min_outcome_confidence):.3f}",
            "--min-outcome-match-confidence",
            f"{float(min_outcome_match_confidence):.3f}",
            "--min-ponder-label-confidence",
            f"{float(min_ponder_label_confidence):.3f}",
            "--train-hybrid" if bool(args.train_hybrid) else "--no-train-hybrid",
            "--train-ponder" if bool(args.train_ponder) else "--no-train-ponder",
        ]
        train_rc = _run_cmd(
            train_cmd,
            root,
            timeout_sec=int(args.train_timeout_sec),
            stop_file=stop_file,
            heartbeat=_heartbeat,
            heartbeat_stage="train",
            heartbeat_interval_sec=int(args.heartbeat_interval_sec),
        )
        train_summary_path = train_root / train_run_id / "summary.json"
        train_summary = _json_load(train_summary_path)
        if train_rc != 0 or not train_summary:
            state["cycles_done"] = cycle
            state["cycles_failed"] = int(state.get("cycles_failed", 0)) + 1
            if train_rc == 130:
                state["last_status"] = "stopped"
            elif train_rc == 124:
                state["last_status"] = "train_timeout"
            else:
                state["last_status"] = "train_failed"
            state["last_cycle"] = cycle_id
            state["updated_at"] = _now_iso()
            _json_dump(state_path, state)
            _append_jsonl(
                history_path,
                {
                    "ts": _now_iso(),
                    "cycle_id": cycle_id,
                    "status": state["last_status"],
                    "train_rc": train_rc,
                    "train_summary_path": str(train_summary_path),
                },
            )
            if train_rc == 130:
                break
            if int(args.interval_sec) > 0:
                time.sleep(int(args.interval_sec))
            continue

        learning_root = Path(str(train_summary.get("pipeline_output_root", "")).strip())
        if not learning_root.is_absolute():
            learning_root = (root / learning_root).resolve()
        latest_lr = _latest_learning_run(learning_root)
        if latest_lr is None:
            state["cycles_done"] = cycle
            state["cycles_failed"] = int(state.get("cycles_failed", 0)) + 1
            state["last_status"] = "learning_missing"
            state["last_cycle"] = cycle_id
            state["updated_at"] = _now_iso()
            _json_dump(state_path, state)
            _append_jsonl(
                history_path,
                {"ts": _now_iso(), "cycle_id": cycle_id, "status": "learning_missing", "learning_root": str(learning_root)},
            )
            if int(args.interval_sec) > 0:
                time.sleep(int(args.interval_sec))
            continue

        candidate_hybrid = (latest_lr / "weights" / "hybrid_weights.json").resolve()
        candidate_ponder = (latest_lr / "weights" / "ponder_gate_weights.json").resolve()
        if not candidate_hybrid.exists():
            state["cycles_done"] = cycle
            state["cycles_failed"] = int(state.get("cycles_failed", 0)) + 1
            state["last_status"] = "candidate_missing"
            state["last_cycle"] = cycle_id
            state["updated_at"] = _now_iso()
            _json_dump(state_path, state)
            _append_jsonl(
                history_path,
                {
                    "ts": _now_iso(),
                    "cycle_id": cycle_id,
                    "status": "candidate_missing",
                    "candidate_hybrid": str(candidate_hybrid),
                },
            )
            if int(args.interval_sec) > 0:
                time.sleep(int(args.interval_sec))
            continue

        ab_root = cycle_dir / "ab"
        old_opts = _wrapper_options_for_eval(old_hybrid_snapshot.resolve(), old_ponder_snapshot.resolve() if old_ponder_snapshot else None)
        cand_opts = _wrapper_options_for_eval(candidate_hybrid, candidate_ponder if candidate_ponder.exists() else None)

        old_cmd = [
            "python3",
            "scripts/run_selfplay_dataset.py",
            "--games",
            str(int(ab_games)),
            "--nodes",
            str(int(nodes)),
            "--max-plies",
            str(int(max_plies)),
            "--seed",
            str(int(seed)),
            "--opening-file",
            str(fixed_openings),
            "--opening-plies",
            str(int(opening_plies)),
            "--output-root",
            str(ab_root),
            "--run-id",
            "ab_old",
            "--no-auto-pipeline",
            "--wrapper-options",
            old_opts,
        ]
        cand_cmd = [
            "python3",
            "scripts/run_selfplay_dataset.py",
            "--games",
            str(int(ab_games)),
            "--nodes",
            str(int(nodes)),
            "--max-plies",
            str(int(max_plies)),
            "--seed",
            str(int(seed)),
            "--opening-file",
            str(fixed_openings),
            "--opening-plies",
            str(int(opening_plies)),
            "--output-root",
            str(ab_root),
            "--run-id",
            "ab_cand",
            "--no-auto-pipeline",
            "--wrapper-options",
            cand_opts,
        ]
        old_rc = _run_cmd(
            old_cmd,
            root,
            timeout_sec=int(args.ab_timeout_sec),
            stop_file=stop_file,
            heartbeat=_heartbeat,
            heartbeat_stage="ab_old",
            heartbeat_interval_sec=int(args.heartbeat_interval_sec),
        )
        if old_rc == 130:
            state["cycles_done"] = cycle
            state["cycles_failed"] = int(state.get("cycles_failed", 0)) + 1
            state["last_status"] = "stopped"
            state["last_cycle"] = cycle_id
            state["updated_at"] = _now_iso()
            _json_dump(state_path, state)
            _append_jsonl(
                history_path,
                {"ts": _now_iso(), "cycle_id": cycle_id, "status": "stopped", "ab_old_rc": old_rc},
            )
            break

        cand_rc = _run_cmd(
            cand_cmd,
            root,
            timeout_sec=int(args.ab_timeout_sec),
            stop_file=stop_file,
            heartbeat=_heartbeat,
            heartbeat_stage="ab_cand",
            heartbeat_interval_sec=int(args.heartbeat_interval_sec),
        )
        if old_rc != 0 or cand_rc != 0:
            if old_rc == 124 or cand_rc == 124:
                status = "ab_timeout"
            elif old_rc == 130 or cand_rc == 130:
                status = "stopped"
            else:
                status = "ab_failed"
            state["cycles_done"] = cycle
            state["cycles_failed"] = int(state.get("cycles_failed", 0)) + 1
            state["last_status"] = status
            state["last_cycle"] = cycle_id
            state["updated_at"] = _now_iso()
            _json_dump(state_path, state)
            _append_jsonl(
                history_path,
                {"ts": _now_iso(), "cycle_id": cycle_id, "status": status, "ab_old_rc": old_rc, "ab_cand_rc": cand_rc},
            )
            if status == "stopped":
                break
            if int(args.interval_sec) > 0:
                time.sleep(int(args.interval_sec))
            continue

        m_old = _collect_metrics(ab_root / "ab_old", "ab_old")
        m_cand = _collect_metrics(ab_root / "ab_cand", "ab_cand")

        delta = {
            "wrapper_win_rate": m_cand.wrapper_win_rate - m_old.wrapper_win_rate,
            "actual_move_topk_rate": m_cand.actual_move_topk_rate - m_old.actual_move_topk_rate,
            "verify_error_events": m_cand.verify_error_events - m_old.verify_error_events,
        }
        adopt = (
            delta["wrapper_win_rate"] >= float(args.gate_min_winrate_delta)
            and delta["actual_move_topk_rate"] >= float(args.gate_min_topk_delta)
            and delta["verify_error_events"] <= int(args.gate_max_verify_error_delta)
        )

        next_action = _propose_and_decide_next_action(adopt=bool(adopt), delta=delta, runtime_params=rp)
        state["runtime_params"] = dict(next_action.get("runtime_params") or rp)

        if adopt:
            shutil.copy2(candidate_hybrid, hybrid_model)
            if candidate_ponder.exists():
                shutil.copy2(candidate_ponder, ponder_model)

        report = {
            "cycle_id": cycle_id,
            "fixed_openings": str(fixed_openings),
            "train_summary": str(train_summary_path),
            "candidate_hybrid": str(candidate_hybrid),
            "candidate_ponder": str(candidate_ponder) if candidate_ponder.exists() else "",
            "old_metrics": _to_dict(m_old),
            "cand_metrics": _to_dict(m_cand),
            "delta": delta,
            "gate": {
                "min_winrate_delta": float(args.gate_min_winrate_delta),
                "min_topk_delta": float(args.gate_min_topk_delta),
                "max_verify_error_delta": int(args.gate_max_verify_error_delta),
            },
            "adopt": bool(adopt),
            "next_action": next_action,
            "completed_at": _now_iso(),
        }
        report_path = cycle_dir / "reports" / "adoption_gate_report.json"
        _json_dump(report_path, report)
        summary_md_path = cycle_dir / "reports" / "cycle_summary.md"
        summary_md = _summarize_cycle_markdown(
            cycle_id=cycle_id,
            adopt=bool(adopt),
            old_m=m_old,
            cand_m=m_cand,
            delta=delta,
            gate=state.get("gate", {}),
            next_action=next_action,
        )
        summary_md_path.parent.mkdir(parents=True, exist_ok=True)
        summary_md_path.write_text(summary_md, encoding="utf-8")

        state["cycles_done"] = cycle
        if adopt:
            state["cycles_adopted"] = int(state.get("cycles_adopted", 0)) + 1
        state["last_status"] = "adopted" if adopt else "rejected"
        state["last_cycle"] = cycle_id
        state["last_report"] = str(report_path)
        state["last_summary_md"] = str(summary_md_path)
        state["last_next_action"] = next_action
        state["updated_at"] = _now_iso()
        state["current_stage"] = "idle"
        _json_dump(state_path, state)
        _append_jsonl(
            history_path,
            {
                "ts": _now_iso(),
                "cycle_id": cycle_id,
                "status": state["last_status"],
                "report_path": str(report_path),
                "summary_md": str(summary_md_path),
                "adopt": bool(adopt),
                "delta": delta,
                "next_action": next_action,
            },
        )
        print(f"[autopilot] cycle={cycle_id} status={state['last_status']} adopt={adopt}")

        if int(args.interval_sec) > 0:
            _heartbeat("sleep")
            time.sleep(int(args.interval_sec))

    print("[autopilot] finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
