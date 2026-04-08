#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _json_load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve(root: Path, raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (root / p).resolve()
    return p


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _latest_learning_run(learning_root: Path) -> Path | None:
    if not learning_root.exists():
        return None
    dirs = sorted([p for p in learning_root.iterdir() if p.is_dir()], key=lambda x: x.name)
    return dirs[-1] if dirs else None


def _run_with_monitor(
    *,
    cmd: list[str],
    cwd: Path,
    stall_dir: Path | None = None,
    stall_threshold_sec: int = 600,
    poll_sec: int = 60,
    timeout_sec: int = 0,
) -> tuple[int, bool, bool]:
    print(f"[goal] run: {' '.join(shlex.quote(x) for x in cmd)}")
    start = time.time()
    proc = subprocess.Popen(cmd, cwd=str(cwd), text=True)  # noqa: S603,S607
    stall_hit = False
    killed = False
    last_count = -1
    last_progress_ts = time.time()

    while True:
        rc = proc.poll()
        now = time.time()
        if rc is not None:
            return int(rc), stall_hit, killed

        if timeout_sec > 0 and (now - start) > timeout_sec:
            stall_hit = True
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=8)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return 124, True, True

        if stall_dir is not None:
            count = len(list(stall_dir.rglob("*.kif"))) if stall_dir.exists() else 0
            if count != last_count:
                last_count = count
                last_progress_ts = now
            elif (now - last_progress_ts) >= max(60, int(stall_threshold_sec)):
                stall_hit = True
                try:
                    proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                killed = True
                return 125, True, killed

        # Keep process polling responsive; stall detection still uses stall_threshold_sec.
        sleep_sec = max(1, min(5, int(poll_sec)))
        time.sleep(sleep_sec)


def _champion_cmd(
    *,
    root: Path,
    champion_script: Path,
    run_id: str,
    output_root: Path,
    current_hybrid: Path,
    candidate_hybrid: Path,
    models_hybrid: Path,
    snapshot_dir: Path,
    games: int,
    nodes: int,
    max_plies: int,
    opening_file: Path,
    opening_plies: int,
    seed: int,
    gate_min_winrate_delta: float,
    gate_min_topk_delta: float,
    gate_max_verify_error_delta: int,
    selfplay_script: Path,
    wrapper_cmd: str,
    backend_engine: str,
    backend_eval: str,
    backend_args: str,
    backend_options: str,
    selfplay_think_timeout_sec: float,
    selfplay_game_walltime_sec: float,
    adopt: bool,
) -> list[str]:
    cmd = [
        "python3",
        str(champion_script),
        "--current-hybrid",
        str(current_hybrid),
        "--candidate-hybrid",
        str(candidate_hybrid),
        "--models-hybrid-path",
        str(models_hybrid),
        "--snapshot-dir",
        str(snapshot_dir),
        "--output-root",
        str(output_root),
        "--run-id",
        run_id,
        "--games",
        str(int(games)),
        "--nodes",
        str(int(nodes)),
        "--max-plies",
        str(int(max_plies)),
        "--opening-file",
        str(opening_file),
        "--opening-plies",
        str(int(opening_plies)),
        "--seed",
        str(int(seed)),
        "--gate-min-winrate-delta",
        str(float(gate_min_winrate_delta)),
        "--gate-min-topk-delta",
        str(float(gate_min_topk_delta)),
        "--gate-max-verify-error-delta",
        str(int(gate_max_verify_error_delta)),
        "--selfplay-script",
        str(selfplay_script),
        "--wrapper-cmd",
        wrapper_cmd,
        "--backend-engine",
        backend_engine,
        "--backend-eval",
        backend_eval,
        "--backend-args",
        backend_args,
        "--backend-options",
        backend_options,
        "--selfplay-think-timeout-sec",
        f"{float(selfplay_think_timeout_sec):.3f}",
        "--selfplay-game-walltime-sec",
        f"{float(selfplay_game_walltime_sec):.3f}",
        "--adopt" if adopt else "--no-adopt",
    ]
    return cmd


def _retrain_cmd(
    *,
    selfplay_script: Path,
    retrain_root: Path,
    games: int,
    nodes: int,
    max_plies: int,
    opening_file: Path,
    opening_plies: int,
    seed: int,
    wrapper_cmd: str,
    backend_engine: str,
    backend_eval: str,
    backend_args: str,
    backend_options: str,
) -> list[str]:
    return [
        "python3",
        str(selfplay_script),
        "--games",
        str(int(games)),
        "--nodes",
        str(int(nodes)),
        "--max-plies",
        str(int(max_plies)),
        "--seed",
        str(int(seed)),
        "--opening-file",
        str(opening_file),
        "--opening-plies",
        str(int(opening_plies)),
        "--output-root",
        str(retrain_root),
        "--run-id",
        "train",
        "--wrapper-cmd",
        wrapper_cmd,
        "--backend-engine",
        backend_engine,
        "--backend-eval",
        backend_eval,
        "--backend-args",
        backend_args,
        "--backend-options",
        backend_options,
        "--train-hybrid",
        "--no-train-ponder",
        "--min-outcome-confidence",
        "0.400",
        "--min-outcome-match-confidence",
        "0.400",
        "--min-ponder-label-confidence",
        "0.000",
    ]


def _smoke_cmd(
    *,
    smoke_script: Path,
    engine: str,
    eval_dir: str,
    wrapper_cmd: str,
) -> list[str]:
    return [
        "python3",
        str(smoke_script),
        "--engine",
        engine,
        "--eval",
        eval_dir,
        "--wrapper",
        wrapper_cmd,
        "--verify-mode",
        "VERIFY_ONLY",
        "--verify-hybrid-policy",
        "CONSERVATIVE",
        "--mate-profile",
        "SAFE",
        "--movetime",
        "300",
    ]


def _final_summary(final_decision: dict[str, Any]) -> str:
    lines = [
        f"# Champion Goal Summary: {final_decision.get('run_id','')}",
        "",
        f"- status: **{final_decision.get('status','failed')}**",
        f"- decision_reason: `{final_decision.get('decision_reason','')}`",
        f"- cycles_total: `{final_decision.get('cycles_total',0)}`",
        f"- non_adopt_streak: `{final_decision.get('non_adopt_streak',0)}`",
        f"- champion_path: `{final_decision.get('champion_path','')}`",
        f"- smoke_ok: `{bool(final_decision.get('smoke_ok', False))}`",
        f"- best_cycle_report_path: `{final_decision.get('best_cycle_report_path','')}`",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Goal closure orchestrator for champion confirmation / stop criteria.")
    parser.add_argument("--current-hybrid", default="models/hybrid_weights.json")
    parser.add_argument("--seed-candidate-hybrid", required=True)
    parser.add_argument("--output-root", default="artifacts_local/champion_goal")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--games-main", type=int, default=700)
    parser.add_argument("--games-confirm", type=int, default=500)
    parser.add_argument("--retrain-games", type=int, default=120)
    parser.add_argument("--nodes", type=int, default=800)
    parser.add_argument("--max-plies", type=int, default=140)
    parser.add_argument("--opening-file", default="artifacts_local/benchmarks/fixed_bench_openings_v1.txt")
    parser.add_argument("--opening-plies", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260227)
    parser.add_argument("--gate-min-winrate-delta", type=float, default=0.02)
    parser.add_argument("--gate-min-topk-delta", type=float, default=-0.01)
    parser.add_argument("--gate-max-verify-error-delta", type=int, default=1)
    parser.add_argument("--max-cycles", type=int, default=4)
    parser.add_argument("--max-non-adopt-streak", type=int, default=3)
    parser.add_argument("--selfplay-think-timeout-sec", type=float, default=4.0)
    parser.add_argument("--selfplay-game-walltime-sec", type=float, default=90.0)
    parser.add_argument("--champion-timeout-sec", type=int, default=14400)
    parser.add_argument("--retrain-timeout-sec", type=int, default=7200)
    parser.add_argument("--confirm-timeout-sec", type=int, default=10800)
    parser.add_argument("--auto-smoke", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--adopt", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--champion-script", default="scripts/run_champion_ab_gate.py")
    parser.add_argument("--selfplay-script", default="scripts/run_selfplay_dataset.py")
    parser.add_argument("--smoke-script", default="scripts/smoke_real_engine.py")
    parser.add_argument("--wrapper-cmd", default="python3 -m taso_swindle.main")
    parser.add_argument("--backend-engine", default="./YaneuraOu")
    parser.add_argument("--backend-eval", default="./eval")
    parser.add_argument("--backend-args", default="")
    parser.add_argument("--backend-options", default="")
    parser.add_argument("--stall-threshold-sec", type=int, default=600)
    parser.add_argument("--stall-poll-sec", type=int, default=60)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output_root = _resolve(root, args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id.strip() or f"champion-goal-{_ts()}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state_path = run_dir / "state.json"
    history_path = run_dir / "history.jsonl"
    final_decision_path = run_dir / "final_decision.json"
    final_summary_path = run_dir / "final_summary.md"

    current_hybrid = _resolve(root, args.current_hybrid)
    seed_candidate_hybrid = _resolve(root, args.seed_candidate_hybrid)
    models_hybrid = _resolve(root, "models/hybrid_weights.json")
    snapshot_dir = _resolve(root, "models/snapshots")
    opening_file = _resolve(root, args.opening_file)
    champion_script = _resolve(root, args.champion_script)
    selfplay_script = _resolve(root, args.selfplay_script)
    smoke_script = _resolve(root, args.smoke_script)

    if not current_hybrid.exists():
        raise SystemExit(f"missing current hybrid: {current_hybrid}")
    if not seed_candidate_hybrid.exists():
        raise SystemExit(f"missing seed candidate hybrid: {seed_candidate_hybrid}")
    if not opening_file.exists():
        raise SystemExit(f"missing opening file: {opening_file}")

    state: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "updated_at": _now_iso(),
        "status": "running",
        "cycle_index": 0,
        "non_adopt_streak": 0,
        "max_cycles": int(args.max_cycles),
        "max_non_adopt_streak": int(args.max_non_adopt_streak),
        "candidate_path": str(seed_candidate_hybrid),
        "best_cycle_report_path": "",
        "smoke_ok": False,
    }
    _json_dump(state_path, state)

    candidate_path = seed_candidate_hybrid
    best_cycle_report = ""
    smoke_ok = False

    def _persist_state() -> None:
        state["updated_at"] = _now_iso()
        _json_dump(state_path, state)

    for cycle_idx in range(1, int(args.max_cycles) + 1):
        state["cycle_index"] = cycle_idx
        state["current_stage"] = "main_ab"
        _persist_state()

        cycle_id = f"cycle-{cycle_idx:03d}-{_ts()}"
        cycle_dir = run_dir / cycle_id
        cycle_dir.mkdir(parents=True, exist_ok=True)
        main_run_id = f"{cycle_id}-main"
        main_cmd = _champion_cmd(
            root=root,
            champion_script=champion_script,
            run_id=main_run_id,
            output_root=cycle_dir,
            current_hybrid=models_hybrid,
            candidate_hybrid=candidate_path,
            models_hybrid=models_hybrid,
            snapshot_dir=snapshot_dir,
            games=int(args.games_main),
            nodes=int(args.nodes),
            max_plies=int(args.max_plies),
            opening_file=opening_file,
            opening_plies=int(args.opening_plies),
            seed=int(args.seed),
            gate_min_winrate_delta=float(args.gate_min_winrate_delta),
            gate_min_topk_delta=float(args.gate_min_topk_delta),
            gate_max_verify_error_delta=int(args.gate_max_verify_error_delta),
            selfplay_script=selfplay_script,
            wrapper_cmd=args.wrapper_cmd,
            backend_engine=args.backend_engine,
            backend_eval=args.backend_eval,
            backend_args=args.backend_args,
            backend_options=args.backend_options,
            selfplay_think_timeout_sec=float(args.selfplay_think_timeout_sec),
            selfplay_game_walltime_sec=float(args.selfplay_game_walltime_sec),
            adopt=bool(args.adopt),
        )
        stall_dir = cycle_dir / main_run_id / "selfplay_ab"
        rc, stall_hit, killed = _run_with_monitor(
            cmd=main_cmd,
            cwd=root,
            stall_dir=stall_dir,
            stall_threshold_sec=int(args.stall_threshold_sec),
            poll_sec=int(args.stall_poll_sec),
            timeout_sec=int(args.champion_timeout_sec),
        )
        main_report = cycle_dir / main_run_id / "reports" / "champion_ab_report.json"
        if rc in {124, 125} and stall_hit:
            retry_id = f"{main_run_id}-retry1"
            retry_cmd = _champion_cmd(
                root=root,
                champion_script=champion_script,
                run_id=retry_id,
                output_root=cycle_dir,
                current_hybrid=models_hybrid,
                candidate_hybrid=candidate_path,
                models_hybrid=models_hybrid,
                snapshot_dir=snapshot_dir,
                games=int(args.games_main),
                nodes=int(args.nodes),
                max_plies=int(args.max_plies),
                opening_file=opening_file,
                opening_plies=int(args.opening_plies),
                seed=int(args.seed) + 1,
                gate_min_winrate_delta=float(args.gate_min_winrate_delta),
                gate_min_topk_delta=float(args.gate_min_topk_delta),
                gate_max_verify_error_delta=int(args.gate_max_verify_error_delta),
                selfplay_script=selfplay_script,
                wrapper_cmd=args.wrapper_cmd,
                backend_engine=args.backend_engine,
                backend_eval=args.backend_eval,
                backend_args=args.backend_args,
                backend_options=args.backend_options,
                selfplay_think_timeout_sec=float(args.selfplay_think_timeout_sec),
                selfplay_game_walltime_sec=float(args.selfplay_game_walltime_sec),
                adopt=bool(args.adopt),
            )
            rc, _, _ = _run_with_monitor(
                cmd=retry_cmd,
                cwd=root,
                stall_dir=cycle_dir / retry_id / "selfplay_ab",
                stall_threshold_sec=int(args.stall_threshold_sec),
                poll_sec=int(args.stall_poll_sec),
                timeout_sec=int(args.champion_timeout_sec),
            )
            main_report = cycle_dir / retry_id / "reports" / "champion_ab_report.json"

        if rc != 0 or not main_report.exists():
            final = {
                "run_id": run_id,
                "finished_at": _now_iso(),
                "status": "failed",
                "champion_path": str(models_hybrid),
                "champion_sha256": _sha256(models_hybrid) if models_hybrid.exists() else "",
                "cycles_total": cycle_idx,
                "non_adopt_streak": state["non_adopt_streak"],
                "decision_reason": "main_ab_failed",
                "best_cycle_report_path": best_cycle_report,
                "smoke_ok": smoke_ok,
                "gate": {
                    "min_winrate_delta": float(args.gate_min_winrate_delta),
                    "min_topk_delta": float(args.gate_min_topk_delta),
                    "max_verify_error_delta": int(args.gate_max_verify_error_delta),
                },
            }
            _json_dump(final_decision_path, final)
            final_summary_path.write_text(_final_summary(final), encoding="utf-8")
            state["status"] = "failed"
            state["failed_stage"] = "main_ab"
            _persist_state()
            return 1

        main = _json_load(main_report)
        best_cycle_report = str(main_report)
        _append_jsonl(
            history_path,
            {
                "ts": _now_iso(),
                "cycle": cycle_idx,
                "stage": "main_ab",
                "report_path": str(main_report),
                "winner": main.get("winner"),
                "adopted": bool(main.get("adopted", False)),
                "delta": main.get("delta", {}),
            },
        )

        if bool(main.get("candidate_pass", False)):
            state["current_stage"] = "confirm_ab"
            _persist_state()
            confirm_run_id = f"{cycle_id}-confirm"
            prev_snapshot = cycle_dir / f"{cycle_id}-prev_champion.json"
            shutil.copy2(models_hybrid, prev_snapshot)
            confirm_cmd = _champion_cmd(
                root=root,
                champion_script=champion_script,
                run_id=confirm_run_id,
                output_root=cycle_dir,
                current_hybrid=prev_snapshot,
                candidate_hybrid=models_hybrid,
                models_hybrid=models_hybrid,
                snapshot_dir=snapshot_dir,
                games=int(args.games_confirm),
                nodes=int(args.nodes),
                max_plies=int(args.max_plies),
                opening_file=opening_file,
                opening_plies=int(args.opening_plies),
                seed=int(args.seed) + 5,
                gate_min_winrate_delta=float(args.gate_min_winrate_delta),
                gate_min_topk_delta=float(args.gate_min_topk_delta),
                gate_max_verify_error_delta=int(args.gate_max_verify_error_delta),
                selfplay_script=selfplay_script,
                wrapper_cmd=args.wrapper_cmd,
                backend_engine=args.backend_engine,
                backend_eval=args.backend_eval,
                backend_args=args.backend_args,
                backend_options=args.backend_options,
                selfplay_think_timeout_sec=float(args.selfplay_think_timeout_sec),
                selfplay_game_walltime_sec=float(args.selfplay_game_walltime_sec),
                adopt=bool(args.adopt),
            )
            rc_c, _, _ = _run_with_monitor(
                cmd=confirm_cmd,
                cwd=root,
                stall_dir=cycle_dir / confirm_run_id / "selfplay_ab",
                stall_threshold_sec=int(args.stall_threshold_sec),
                poll_sec=int(args.stall_poll_sec),
                timeout_sec=int(args.confirm_timeout_sec),
            )
            confirm_report = cycle_dir / confirm_run_id / "reports" / "champion_ab_report.json"
            if rc_c != 0 or not confirm_report.exists():
                shutil.copy2(prev_snapshot, models_hybrid)
                state["non_adopt_streak"] = int(state.get("non_adopt_streak", 0)) + 1
                _append_jsonl(
                    history_path,
                    {
                        "ts": _now_iso(),
                        "cycle": cycle_idx,
                        "stage": "confirm_ab",
                        "status": "failed_or_missing",
                        "report_path": str(confirm_report),
                    },
                )
            else:
                conf = _json_load(confirm_report)
                _append_jsonl(
                    history_path,
                    {
                        "ts": _now_iso(),
                        "cycle": cycle_idx,
                        "stage": "confirm_ab",
                        "adopted": bool(conf.get("adopted", False)),
                        "candidate_pass": bool(conf.get("candidate_pass", False)),
                        "report_path": str(confirm_report),
                    },
                )
                if bool(conf.get("candidate_pass", False)) and bool(args.auto_smoke):
                    state["current_stage"] = "smoke"
                    _persist_state()
                    rc_s, _, _ = _run_with_monitor(
                        cmd=_smoke_cmd(
                            smoke_script=smoke_script,
                            engine=args.backend_engine,
                            eval_dir=args.backend_eval,
                            wrapper_cmd=args.wrapper_cmd,
                        ),
                        cwd=root,
                        timeout_sec=600,
                    )
                    smoke_ok = (rc_s == 0)
                else:
                    smoke_ok = bool(not args.auto_smoke)

                if bool(conf.get("candidate_pass", False)) and smoke_ok:
                    final = {
                        "run_id": run_id,
                        "finished_at": _now_iso(),
                        "status": "final_champion_confirmed",
                        "champion_path": str(models_hybrid),
                        "champion_sha256": _sha256(models_hybrid),
                        "cycles_total": cycle_idx,
                        "non_adopt_streak": int(state.get("non_adopt_streak", 0)),
                        "decision_reason": "main_and_confirm_pass_with_smoke",
                        "best_cycle_report_path": str(confirm_report),
                        "smoke_ok": smoke_ok,
                        "gate": {
                            "min_winrate_delta": float(args.gate_min_winrate_delta),
                            "min_topk_delta": float(args.gate_min_topk_delta),
                            "max_verify_error_delta": int(args.gate_max_verify_error_delta),
                        },
                    }
                    _json_dump(final_decision_path, final)
                    final_summary_path.write_text(_final_summary(final), encoding="utf-8")
                    state["status"] = "done"
                    state["result"] = "final_champion_confirmed"
                    state["best_cycle_report_path"] = str(confirm_report)
                    state["smoke_ok"] = smoke_ok
                    _persist_state()
                    return 0
                else:
                    shutil.copy2(prev_snapshot, models_hybrid)
                    state["non_adopt_streak"] = int(state.get("non_adopt_streak", 0)) + 1
        else:
            state["current_stage"] = "retrain"
            _persist_state()
            failure_openings = _resolve(root, str(main.get("failure_openings_path", "")))
            if failure_openings.exists():
                retrain_root = cycle_dir / "failure_retrain"
                rc_r, _, _ = _run_with_monitor(
                    cmd=_retrain_cmd(
                        selfplay_script=selfplay_script,
                        retrain_root=retrain_root,
                        games=int(args.retrain_games),
                        nodes=int(args.nodes),
                        max_plies=int(args.max_plies),
                        opening_file=failure_openings,
                        opening_plies=int(args.opening_plies),
                        seed=int(args.seed) + 17,
                        wrapper_cmd=args.wrapper_cmd,
                        backend_engine=args.backend_engine,
                        backend_eval=args.backend_eval,
                        backend_args=args.backend_args,
                        backend_options=args.backend_options,
                    ),
                    cwd=root,
                    stall_dir=retrain_root / "train" / "games_kif",
                    stall_threshold_sec=int(args.stall_threshold_sec),
                    poll_sec=int(args.stall_poll_sec),
                    timeout_sec=int(args.retrain_timeout_sec),
                )
                if rc_r == 0:
                    train_summary = _json_load(retrain_root / "train" / "summary.json")
                    learning_root_raw = str(train_summary.get("pipeline_output_root", "")).strip()
                    learning_root = _resolve(root, learning_root_raw) if learning_root_raw else Path("")
                    latest_lr = _latest_learning_run(learning_root) if learning_root_raw else None
                    retrained = (latest_lr / "weights/hybrid_weights.json").resolve() if latest_lr else Path("")
                    if retrained.exists():
                        candidate_path = retrained
                    else:
                        state["non_adopt_streak"] = int(state.get("non_adopt_streak", 0)) + 1
                else:
                    state["non_adopt_streak"] = int(state.get("non_adopt_streak", 0)) + 1
            else:
                state["non_adopt_streak"] = int(state.get("non_adopt_streak", 0)) + 1

        _persist_state()
        if int(state.get("non_adopt_streak", 0)) >= int(args.max_non_adopt_streak):
            final = {
                "run_id": run_id,
                "finished_at": _now_iso(),
                "status": "completed_plateau",
                "champion_path": str(models_hybrid),
                "champion_sha256": _sha256(models_hybrid),
                "cycles_total": cycle_idx,
                "non_adopt_streak": int(state.get("non_adopt_streak", 0)),
                "decision_reason": "max_non_adopt_streak_reached",
                "best_cycle_report_path": best_cycle_report,
                "smoke_ok": smoke_ok,
                "gate": {
                    "min_winrate_delta": float(args.gate_min_winrate_delta),
                    "min_topk_delta": float(args.gate_min_topk_delta),
                    "max_verify_error_delta": int(args.gate_max_verify_error_delta),
                },
            }
            _json_dump(final_decision_path, final)
            final_summary_path.write_text(_final_summary(final), encoding="utf-8")
            state["status"] = "done"
            state["result"] = "completed_plateau"
            _persist_state()
            return 0

    final = {
        "run_id": run_id,
        "finished_at": _now_iso(),
        "status": "completed_plateau",
        "champion_path": str(models_hybrid),
        "champion_sha256": _sha256(models_hybrid),
        "cycles_total": int(args.max_cycles),
        "non_adopt_streak": int(state.get("non_adopt_streak", 0)),
        "decision_reason": "max_cycles_reached",
        "best_cycle_report_path": best_cycle_report,
        "smoke_ok": smoke_ok,
        "gate": {
            "min_winrate_delta": float(args.gate_min_winrate_delta),
            "min_topk_delta": float(args.gate_min_topk_delta),
            "max_verify_error_delta": int(args.gate_max_verify_error_delta),
        },
    }
    _json_dump(final_decision_path, final)
    final_summary_path.write_text(_final_summary(final), encoding="utf-8")
    state["status"] = "done"
    state["result"] = "completed_plateau"
    _persist_state()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
