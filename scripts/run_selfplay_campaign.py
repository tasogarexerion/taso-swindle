#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts_id() -> str:
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


def _latest_learning_run(learning_root: Path) -> Optional[Path]:
    if not learning_root.exists():
        return None
    runs = [p for p in learning_root.iterdir() if p.is_dir()]
    if not runs:
        return None
    runs.sort(key=lambda p: p.name, reverse=True)
    return runs[0]


@dataclass(frozen=True)
class QualityGate:
    min_actual_coverage: float
    min_outcome_coverage: float
    min_training_records: int

    def evaluate(self, quality: dict[str, Any], learning_summary: dict[str, Any]) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        actual_cov = _safe_float(quality.get("actual_opponent_move_coverage"), 0.0)
        outcome_cov = _safe_float(quality.get("outcome_tag_coverage"), 0.0)
        rec_training = _safe_int(learning_summary.get("records_training"), 0)
        if actual_cov < self.min_actual_coverage:
            reasons.append(f"actual_cov_low:{actual_cov:.3f}")
        if outcome_cov < self.min_outcome_coverage:
            reasons.append(f"outcome_cov_low:{outcome_cov:.3f}")
        if rec_training < self.min_training_records:
            reasons.append(f"training_records_low:{rec_training}")
        return len(reasons) == 0, reasons


def _copy_if_exists(src: Path, dst: Path) -> bool:
    if not src.exists() or not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _build_wrapper_options(hybrid_weights: Path, extra: str, *, enable_adjustment: bool) -> str:
    items: list[str] = []
    if enable_adjustment and hybrid_weights.exists():
        items.append("SwindleUseHybridLearnedAdjustment=true")
        items.append(f"SwindleHybridWeightsPath={hybrid_weights}")
    items.append("SwindleEvalThresholdCp=0")
    raw_extra = (extra or "").strip()
    if raw_extra:
        items.append(raw_extra)
    return ";".join(items)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run quality-gated self-play campaign and auto-adopt weights.")
    parser.add_argument("--target-games", type=int, default=50_000, help="target total games for campaign")
    parser.add_argument("--cycle-games", type=int, default=100, help="games per cycle")
    parser.add_argument("--nodes", type=int, default=400)
    parser.add_argument("--movetime-ms", type=int, default=0)
    parser.add_argument("--max-plies", type=int, default=120)
    parser.add_argument("--max-cycles", type=int, default=0, help="0 means unlimited until target")
    parser.add_argument("--sleep-sec", type=float, default=1.0, help="sleep between cycles")

    parser.add_argument("--output-root", default="artifacts/selfplay_runs")
    parser.add_argument("--campaign-root", default="artifacts/campaign_runs")
    parser.add_argument("--campaign-id", default="", help="optional fixed campaign id")
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--reset-state", action="store_true")

    parser.add_argument("--train-hybrid", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--train-ponder", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--hybrid-model-path", default="models/hybrid_weights.json")
    parser.add_argument("--ponder-model-path", default="models/ponder_gate_weights.json")
    parser.add_argument("--wrapper-options-extra", default="")
    parser.add_argument("--enable-hybrid-adjustment", action=argparse.BooleanOptionalAction, default=True)

    parser.add_argument("--min-actual-coverage", type=float, default=0.80)
    parser.add_argument("--min-outcome-coverage", type=float, default=0.80)
    parser.add_argument("--min-training-records", type=int, default=50)
    parser.add_argument("--min-outcome-confidence", type=float, default=0.60)
    parser.add_argument("--min-outcome-match-confidence", type=float, default=0.60)
    parser.add_argument("--min-ponder-label-confidence", type=float, default=0.00)
    parser.add_argument("--stop-on-quality-fail", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = (root / output_root).resolve()
    campaign_root = Path(args.campaign_root).expanduser()
    if not campaign_root.is_absolute():
        campaign_root = (root / campaign_root).resolve()
    hybrid_model_path = Path(args.hybrid_model_path).expanduser()
    if not hybrid_model_path.is_absolute():
        hybrid_model_path = (root / hybrid_model_path).resolve()
    ponder_model_path = Path(args.ponder_model_path).expanduser()
    if not ponder_model_path.is_absolute():
        ponder_model_path = (root / ponder_model_path).resolve()

    campaign_id = (args.campaign_id or "").strip() or f"campaign-{_ts_id()}"
    camp_dir = campaign_root / campaign_id
    state_path = camp_dir / "state.json"
    history_path = camp_dir / "history.jsonl"

    if args.reset_state and state_path.exists():
        state_path.unlink()

    if bool(args.resume) and state_path.exists():
        state = _json_load(state_path)
    else:
        state = {
            "campaign_id": campaign_id,
            "started_at": _utc_now(),
            "target_games": int(args.target_games),
            "cycle_games": int(args.cycle_games),
            "total_games": 0,
            "cycles_done": 0,
            "cycles_adopted": 0,
            "cycles_quality_failed": 0,
            "cycles_runtime_failed": 0,
            "last_run_id": "",
            "last_status": "initialized",
            "current_hybrid_model": str(hybrid_model_path),
            "current_ponder_model": str(ponder_model_path),
        }
        _json_dump(state_path, state)

    gate = QualityGate(
        min_actual_coverage=float(args.min_actual_coverage),
        min_outcome_coverage=float(args.min_outcome_coverage),
        min_training_records=int(args.min_training_records),
    )

    while int(state.get("total_games", 0)) < int(args.target_games):
        if int(args.max_cycles) > 0 and int(state.get("cycles_done", 0)) >= int(args.max_cycles):
            break

        cycle_idx = int(state.get("cycles_done", 0)) + 1
        run_id = f"{campaign_id}-c{cycle_idx:05d}-{_ts_id()}"
        wrapper_opts = _build_wrapper_options(
            hybrid_weights=hybrid_model_path,
            extra=str(args.wrapper_options_extra),
            enable_adjustment=bool(args.enable_hybrid_adjustment),
        )

        cmd = [
            "python3",
            "scripts/run_selfplay_dataset.py",
            "--run-id",
            run_id,
            "--games",
            str(int(args.cycle_games)),
            "--max-plies",
            str(int(args.max_plies)),
            "--output-root",
            str(output_root),
            "--min-outcome-confidence",
            f"{float(args.min_outcome_confidence):.3f}",
            "--min-outcome-match-confidence",
            f"{float(args.min_outcome_match_confidence):.3f}",
            "--min-ponder-label-confidence",
            f"{float(args.min_ponder_label_confidence):.3f}",
            "--wrapper-options",
            wrapper_opts,
        ]
        if int(args.movetime_ms) > 0:
            cmd.extend(["--movetime-ms", str(int(args.movetime_ms))])
        else:
            cmd.extend(["--nodes", str(int(args.nodes))])
        cmd.append("--train-hybrid" if bool(args.train_hybrid) else "--no-train-hybrid")
        cmd.append("--train-ponder" if bool(args.train_ponder) else "--no-train-ponder")

        print(f"[campaign] cycle={cycle_idx} run_id={run_id}")
        started = time.time()
        proc = subprocess.run(cmd, cwd=str(root), text=True)  # noqa: S603,S607
        elapsed = max(0.0, time.time() - started)

        run_dir = output_root / run_id
        selfplay_summary_path = run_dir / "summary.json"
        selfplay_summary = _json_load(selfplay_summary_path) if selfplay_summary_path.exists() else {}
        pipeline_rc = _safe_int(selfplay_summary.get("pipeline_rc"), 1)
        games_done = _safe_int(selfplay_summary.get("games"), 0)

        learning_root_raw = str(selfplay_summary.get("pipeline_output_root", "")).strip()
        learning_root = Path(learning_root_raw) if learning_root_raw else (run_dir / "learning_runs")
        if not learning_root.is_absolute():
            learning_root = (root / learning_root).resolve()
        learning_run = _latest_learning_run(learning_root)
        learning_summary = _json_load(learning_run / "summary.json") if learning_run is not None else {}

        quality_path = Path(str(learning_summary.get("quality_report_path", "")).strip())
        if quality_path and not quality_path.is_absolute():
            quality_path = (root / quality_path).resolve()
        quality = _json_load(quality_path) if quality_path.exists() else {}

        adopted = False
        quality_ok = False
        quality_reasons: list[str] = []
        if proc.returncode == 0 and pipeline_rc == 0 and learning_summary:
            quality_ok, quality_reasons = gate.evaluate(quality, learning_summary)
            if quality_ok:
                hybrid_src_raw = str(learning_summary.get("hybrid_weights_path", "")).strip()
                ponder_src_raw = str(learning_summary.get("ponder_weights_path", "")).strip()
                hybrid_src = Path(hybrid_src_raw) if hybrid_src_raw else Path("")
                ponder_src = Path(ponder_src_raw) if ponder_src_raw else Path("")
                if hybrid_src and not hybrid_src.is_absolute():
                    hybrid_src = (root / hybrid_src).resolve()
                if ponder_src and not ponder_src.is_absolute():
                    ponder_src = (root / ponder_src).resolve()

                hybrid_ok = _copy_if_exists(hybrid_src, hybrid_model_path) if bool(args.train_hybrid) else True
                ponder_ok = _copy_if_exists(ponder_src, ponder_model_path) if bool(args.train_ponder) else True
                adopted = bool(hybrid_ok and ponder_ok)
                if adopted:
                    state["cycles_adopted"] = int(state.get("cycles_adopted", 0)) + 1
            else:
                state["cycles_quality_failed"] = int(state.get("cycles_quality_failed", 0)) + 1
        else:
            state["cycles_runtime_failed"] = int(state.get("cycles_runtime_failed", 0)) + 1
            quality_reasons.append(f"runtime_failed:rc={proc.returncode}:pipeline_rc={pipeline_rc}")

        state["total_games"] = int(state.get("total_games", 0)) + games_done
        state["cycles_done"] = int(state.get("cycles_done", 0)) + 1
        state["last_run_id"] = run_id
        state["last_status"] = "adopted" if adopted else ("quality_failed" if quality_reasons else "done")
        state["updated_at"] = _utc_now()
        _json_dump(state_path, state)

        hist = {
            "ts": _utc_now(),
            "cycle": cycle_idx,
            "run_id": run_id,
            "elapsed_sec": elapsed,
            "selfplay_rc": int(proc.returncode),
            "pipeline_rc": pipeline_rc,
            "games_done": games_done,
            "total_games": int(state.get("total_games", 0)),
            "quality_ok": quality_ok,
            "quality_reasons": quality_reasons,
            "adopted": adopted,
            "actual_coverage": _safe_float(quality.get("actual_opponent_move_coverage"), 0.0),
            "outcome_coverage": _safe_float(quality.get("outcome_tag_coverage"), 0.0),
            "records_training": _safe_int(learning_summary.get("records_training"), 0),
            "quality_report_path": str(quality_path) if quality_path else "",
            "learning_run": str(learning_run) if learning_run is not None else "",
            "selfplay_summary_path": str(selfplay_summary_path),
        }
        _append_jsonl(history_path, hist)

        print(
            f"[campaign] cycle={cycle_idx} done games={games_done} total={state['total_games']} "
            f"quality_ok={quality_ok} adopted={adopted}"
        )
        if quality_reasons:
            print(f"[campaign] reasons={','.join(quality_reasons)}")

        if quality_reasons and bool(args.stop_on_quality_fail):
            print("[campaign] stop-on-quality-fail triggered")
            break

        if float(args.sleep_sec) > 0:
            time.sleep(float(args.sleep_sec))

    print(f"[campaign] finished total_games={state.get('total_games', 0)} target={args.target_games}")
    print(f"[campaign] state={state_path}")
    print(f"[campaign] history={history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

