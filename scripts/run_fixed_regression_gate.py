#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
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


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _run_cmd(cmd: list[str], cwd: Path, *, timeout_sec: int = 0) -> tuple[int, str, str]:
    print(f"[fixed-gate] run: {' '.join(shlex.quote(x) for x in cmd)}")
    try:
        done = subprocess.run(
            cmd,
            cwd=str(cwd),
            text=True,
            timeout=(int(timeout_sec) if int(timeout_sec) > 0 else None),
        )  # noqa: S603,S607
    except subprocess.TimeoutExpired as e:
        return 124, "", str(e)
    return int(done.returncode), "", ""


def _resolve_path(root: Path, raw: str) -> Path:
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (root / p).resolve()
    return p


def _latest_learning_run(learning_root: Path) -> Path | None:
    if not learning_root.exists():
        return None
    runs = sorted([p for p in learning_root.iterdir() if p.is_dir()], key=lambda x: x.name)
    return runs[-1] if runs else None


def _remove_stop_flags(paths: list[Path]) -> list[str]:
    removed: list[str] = []
    for p in paths:
        if p.exists():
            try:
                p.unlink()
                removed.append(str(p))
            except Exception:
                pass
    return removed


def _summary_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Fixed Regression Gate: {report.get('run_id', '')}")
    lines.append("")
    lines.append(f"- Status: **{report.get('status', 'unknown')}**")
    lines.append(f"- Champion A/B report: `{report.get('champion_report_path', '')}`")
    lines.append(f"- Champion adopted: **{bool(report.get('champion_adopted', False))}**")
    lines.append(f"- Winner: **{report.get('champion_winner', 'unknown')}**")
    if bool(report.get("retrain_attempted", False)):
        lines.append(f"- Retrain attempted: **true**")
        lines.append(f"- Retrain gate report: `{report.get('retrain_report_path', '')}`")
        lines.append(f"- Retrain adopted: **{bool(report.get('retrain_adopted', False))}**")
    else:
        lines.append("- Retrain attempted: **false**")
    lines.append(f"- Smoke requested: **{bool(report.get('smoke_requested', False))}**")
    lines.append(f"- Smoke ok: **{bool(report.get('smoke_ok', False))}**")
    lines.append("")
    lines.append("## Gate Thresholds")
    gate = report.get("gate", {})
    lines.append(f"- min_winrate_delta={float(gate.get('min_winrate_delta', 0.0)):+.4f}")
    lines.append(f"- min_topk_delta={float(gate.get('min_topk_delta', 0.0)):+.4f}")
    lines.append(f"- max_verify_error_delta={int(gate.get('max_verify_error_delta', 0)):+d}")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fixed regression gate entrypoint: champion A/B, optional smoke, optional failure-band retrain."
    )
    parser.add_argument("--current-hybrid", default="models/hybrid_weights.json")
    parser.add_argument("--candidate-hybrid", required=True)
    parser.add_argument("--models-hybrid-path", default="models/hybrid_weights.json")
    parser.add_argument("--snapshot-dir", default="models/snapshots")

    parser.add_argument("--output-root", default="artifacts_local/champion_ab")
    parser.add_argument("--run-id", default="")
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

    parser.add_argument("--champion-script", default="scripts/run_champion_ab_gate.py")
    parser.add_argument("--selfplay-script", default="scripts/run_selfplay_dataset.py")
    parser.add_argument("--wrapper-cmd", default="python3 -m taso_swindle.main")
    parser.add_argument("--backend-engine", default="./YaneuraOu")
    parser.add_argument("--backend-eval", default="./eval")
    parser.add_argument("--backend-args", default="")
    parser.add_argument("--backend-options", default="")

    parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--smoke-script", default="scripts/smoke_real_engine.py")
    parser.add_argument("--smoke-movetime", type=int, default=300)
    parser.add_argument("--smoke-verify-mode", default="VERIFY_ONLY")
    parser.add_argument("--smoke-verify-hybrid-policy", default="CONSERVATIVE")
    parser.add_argument("--smoke-mate-profile", default="SAFE")
    parser.add_argument("--smoke-ponder", action=argparse.BooleanOptionalAction, default=False)

    parser.add_argument("--failure-retrain", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--failure-retrain-games", type=int, default=160)
    parser.add_argument("--failure-retrain-train-hybrid", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--failure-retrain-train-ponder", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--min-outcome-confidence", type=float, default=0.40)
    parser.add_argument("--min-outcome-match-confidence", type=float, default=0.40)
    parser.add_argument("--min-ponder-label-confidence", type=float, default=0.0)
    parser.add_argument("--champion-timeout-sec", type=int, default=0)
    parser.add_argument("--retrain-timeout-sec", type=int, default=0)
    parser.add_argument("--retest-timeout-sec", type=int, default=0)

    parser.add_argument("--clear-stop-flags", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--stop-supervisor", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--supervisor-pattern", default="run_strength_supervisor.py")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output_root = _resolve_path(root, args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id.strip() or f"fixed-regression-{_ts()}"
    run_dir = output_root / run_id
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    models_hybrid = _resolve_path(root, args.models_hybrid_path)
    current_hybrid = _resolve_path(root, args.current_hybrid)
    candidate_hybrid = _resolve_path(root, args.candidate_hybrid)
    opening_file = _resolve_path(root, args.opening_file)
    champion_script = _resolve_path(root, args.champion_script)
    selfplay_script = _resolve_path(root, args.selfplay_script)
    smoke_script = _resolve_path(root, args.smoke_script)
    snapshot_dir = _resolve_path(root, args.snapshot_dir)

    if not current_hybrid.exists():
        raise SystemExit(f"missing current hybrid: {current_hybrid}")
    if not candidate_hybrid.exists():
        raise SystemExit(f"missing candidate hybrid: {candidate_hybrid}")
    if not opening_file.exists():
        raise SystemExit(f"missing opening file: {opening_file}")
    if not champion_script.exists():
        raise SystemExit(f"missing champion script: {champion_script}")
    if not selfplay_script.exists():
        raise SystemExit(f"missing selfplay script: {selfplay_script}")

    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "status": "running",
        "failed_stage": "",
        "gate": {
            "min_winrate_delta": float(args.gate_min_winrate_delta),
            "min_topk_delta": float(args.gate_min_topk_delta),
            "max_verify_error_delta": int(args.gate_max_verify_error_delta),
        },
        "smoke_requested": bool(args.smoke),
        "retrain_attempted": False,
        "events": [],
        "partial_outputs": [],
    }

    if bool(args.clear_stop_flags):
        removed = _remove_stop_flags(
            [
                root / "artifacts_local/strength_autopilot/STOP",
                root / "artifacts_local/strength_autopilot/SUPERVISOR_STOP",
            ]
        )
        if removed:
            report["events"].append(f"removed_stop_flags:{len(removed)}")
            report["removed_stop_flags"] = removed

    if bool(args.stop_supervisor):
        stop_cmd = ["pkill", "-f", args.supervisor_pattern]
        rc, _, _ = _run_cmd(stop_cmd, root)
        report["supervisor_stop_rc"] = int(rc)

    champion_report_path = reports_dir / "champion_ab_report.json"
    champion_summary_path = reports_dir / "champion_ab_summary.md"
    champion_cmd = [
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
        f"{run_id}-ab",
        "--report-path",
        str(champion_report_path),
        "--summary-path",
        str(champion_summary_path),
        "--games",
        str(int(args.games)),
        "--nodes",
        str(int(args.nodes)),
        "--max-plies",
        str(int(args.max_plies)),
        "--opening-file",
        str(opening_file),
        "--opening-plies",
        str(int(args.opening_plies)),
        "--seed",
        str(int(args.seed)),
        "--gate-min-winrate-delta",
        str(float(args.gate_min_winrate_delta)),
        "--gate-min-topk-delta",
        str(float(args.gate_min_topk_delta)),
        "--gate-max-verify-error-delta",
        str(int(args.gate_max_verify_error_delta)),
        "--selfplay-script",
        str(selfplay_script),
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
        "--adopt" if bool(args.adopt) else "--no-adopt",
    ]
    rc, _, _ = _run_cmd(champion_cmd, root, timeout_sec=int(args.champion_timeout_sec))
    if rc != 0 or not champion_report_path.exists():
        report["status"] = "failed"
        report["failed_stage"] = "champion_ab"
        report["champion_rc"] = int(rc)
        report["partial_outputs"].append({"stage": "champion_ab", "path": str(champion_report_path)})
        report["finished_at"] = _now_iso()
        _json_dump(reports_dir / "fixed_regression_gate_report.json", report)
        (reports_dir / "fixed_regression_gate_summary.md").write_text(_summary_markdown(report), encoding="utf-8")
        return 1

    champion_report = _json_load(champion_report_path)
    report["champion_report_path"] = str(champion_report_path)
    report["partial_outputs"].append({"stage": "champion_ab", "path": str(champion_report_path)})
    report["champion_adopted"] = bool(champion_report.get("adopted", False))
    report["champion_winner"] = champion_report.get("winner", "unknown")
    report["failure_openings_path"] = champion_report.get("failure_openings_path", "")

    if bool(args.smoke) and smoke_script.exists():
        smoke_cmd = [
            "python3",
            str(smoke_script),
            "--engine",
            args.backend_engine,
            "--eval",
            args.backend_eval,
            "--wrapper",
            args.wrapper_cmd,
            "--verify-mode",
            args.smoke_verify_mode,
            "--verify-hybrid-policy",
            args.smoke_verify_hybrid_policy,
            "--mate-profile",
            args.smoke_mate_profile,
            "--movetime",
            str(int(args.smoke_movetime)),
        ]
        if bool(args.smoke_ponder):
            smoke_cmd.append("--ponder")
        smoke_rc, _, _ = _run_cmd(smoke_cmd, root)
        report["smoke_ok"] = bool(smoke_rc == 0)
        report["smoke_rc"] = int(smoke_rc)
        report["partial_outputs"].append({"stage": "smoke", "path": str(smoke_script), "rc": int(smoke_rc)})
    else:
        report["smoke_ok"] = False if bool(args.smoke) else True
        report["smoke_rc"] = -1

    failure_openings_raw = str(champion_report.get("failure_openings_path", "")).strip()
    failure_openings: Path | None = None
    if failure_openings_raw:
        failure_openings = Path(failure_openings_raw)
        if not failure_openings.is_absolute():
            failure_openings = _resolve_path(root, str(failure_openings))
    failure_has_lines = False
    if failure_openings is not None and failure_openings.exists():
        txt = failure_openings.read_text(encoding="utf-8", errors="ignore")
        failure_has_lines = bool([ln for ln in txt.splitlines() if ln.strip()])

    if bool(args.failure_retrain) and failure_openings is not None and failure_openings.exists() and failure_has_lines:
        report["retrain_attempted"] = True
        retrain_root = run_dir / "failure_retrain"
        retrain_run_id = "train"
        retrain_cmd = [
            "python3",
            str(selfplay_script),
            "--games",
            str(int(args.failure_retrain_games)),
            "--nodes",
            str(int(args.nodes)),
            "--max-plies",
            str(int(args.max_plies)),
            "--seed",
            str(int(args.seed) + 17),
            "--opening-file",
            str(failure_openings),
            "--opening-plies",
            str(int(args.opening_plies)),
            "--output-root",
            str(retrain_root),
            "--run-id",
            retrain_run_id,
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
            "--train-hybrid" if bool(args.failure_retrain_train_hybrid) else "--no-train-hybrid",
            "--train-ponder" if bool(args.failure_retrain_train_ponder) else "--no-train-ponder",
            "--min-outcome-confidence",
            f"{float(args.min_outcome_confidence):.3f}",
            "--min-outcome-match-confidence",
            f"{float(args.min_outcome_match_confidence):.3f}",
            "--min-ponder-label-confidence",
            f"{float(args.min_ponder_label_confidence):.3f}",
        ]
        retrain_rc, _, _ = _run_cmd(retrain_cmd, root, timeout_sec=int(args.retrain_timeout_sec))
        report["retrain_rc"] = int(retrain_rc)
        report["partial_outputs"].append({"stage": "retrain", "path": str(retrain_root / retrain_run_id / "summary.json"), "rc": int(retrain_rc)})
        if retrain_rc == 0:
            train_summary = _json_load(retrain_root / retrain_run_id / "summary.json")
            learning_root_raw = str(train_summary.get("pipeline_output_root", "")).strip()
            learning_root = _resolve_path(root, learning_root_raw) if learning_root_raw else Path("")
            latest_lr = _latest_learning_run(learning_root) if learning_root_raw else None
            retrained_hybrid = (latest_lr / "weights/hybrid_weights.json").resolve() if latest_lr else Path("")
            if retrained_hybrid and retrained_hybrid.exists():
                retest_report_path = reports_dir / "failure_retrain_ab_report.json"
                retest_summary_path = reports_dir / "failure_retrain_ab_summary.md"
                retest_cmd = [
                    "python3",
                    str(champion_script),
                    "--current-hybrid",
                    str(models_hybrid),
                    "--candidate-hybrid",
                    str(retrained_hybrid),
                    "--models-hybrid-path",
                    str(models_hybrid),
                    "--snapshot-dir",
                    str(snapshot_dir),
                    "--output-root",
                    str(output_root),
                    "--run-id",
                    f"{run_id}-retest",
                    "--report-path",
                    str(retest_report_path),
                    "--summary-path",
                    str(retest_summary_path),
                    "--games",
                    str(int(args.games)),
                    "--nodes",
                    str(int(args.nodes)),
                    "--max-plies",
                    str(int(args.max_plies)),
                    "--opening-file",
                    str(opening_file),
                    "--opening-plies",
                    str(int(args.opening_plies)),
                    "--seed",
                    str(int(args.seed) + 29),
                    "--gate-min-winrate-delta",
                    str(float(args.gate_min_winrate_delta)),
                    "--gate-min-topk-delta",
                    str(float(args.gate_min_topk_delta)),
                    "--gate-max-verify-error-delta",
                    str(int(args.gate_max_verify_error_delta)),
                    "--selfplay-script",
                    str(selfplay_script),
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
                    "--adopt" if bool(args.adopt) else "--no-adopt",
                ]
                retest_rc, _, _ = _run_cmd(retest_cmd, root, timeout_sec=int(args.retest_timeout_sec))
                report["retrain_retest_rc"] = int(retest_rc)
                report["retrain_report_path"] = str(retest_report_path)
                report["partial_outputs"].append({"stage": "retest", "path": str(retest_report_path), "rc": int(retest_rc)})
                if retest_rc == 0 and retest_report_path.exists():
                    retest_report = _json_load(retest_report_path)
                    report["retrain_adopted"] = bool(retest_report.get("adopted", False))
                else:
                    report["retrain_adopted"] = False
                    report["failed_stage"] = "retest"
            else:
                report["events"].append("retrain_hybrid_missing")
                report["failed_stage"] = "retrain_prepare"
        else:
            report["events"].append("retrain_failed")
            report["failed_stage"] = "retrain"
    else:
        if bool(args.failure_retrain):
            report["events"].append("retrain_skipped_no_failure_band")

    report["finished_at"] = _now_iso()
    report["status"] = "ok"
    final_report_path = reports_dir / "fixed_regression_gate_report.json"
    final_summary_path = reports_dir / "fixed_regression_gate_summary.md"
    _json_dump(final_report_path, report)
    final_summary_path.write_text(_summary_markdown(report), encoding="utf-8")
    print(f"FINAL_REPORT_PATH={final_report_path}")
    print(f"FINAL_SUMMARY_PATH={final_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
