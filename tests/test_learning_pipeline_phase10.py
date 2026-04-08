from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path) -> None:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode not in {0, 1}:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")


def _seed_log(path: Path) -> None:
    rec = {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": "g1",
        "ply": 5,
        "search_id": 12,
        "root_eval_cp": -600,
        "final_bestmove": "7g7f",
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "unknown",
        "verify_mode_used": "VERIFY_ONLY",
        "dfpn_parser_mode": "AUTO",
        "dfpn_parser_hits": [],
        "verify_conflict_count": 0,
        "verify_unknown_count": 0,
        "emergency_fast_mode": False,
        "candidates": [
            {
                "move": "7g7f",
                "base_cp": -220,
                "reply_topk": [{"move": "3c3d"}, {"move": "8c8d"}],
            }
        ],
        "ponder_cache_hit": True,
        "ponder_cache_used": True,
        "ponder_status_summary": "ok",
        "ponder_cache_gate_reason": None,
        "reuse_then_bestmove_changed": False,
        "ponder_label_source": "runtime_observed",
        "ponder_label_confidence": 0.9,
        "ponder_reuse_target": 1.0,
        "actual_opponent_move": "8c8d",
        "actual_move_in_reply_topk": True,
        "actual_move_rank_in_reply_topk": 2,
        "outcome_tag": "swing_success",
        "outcome_confidence": 0.8,
        "ponder_used_budget_ms": 140,
        "ponder_cache_age_ms": 180,
        "backend_restart_count": 0,
    }
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")


def test_learning_pipeline_generates_summary_and_artifacts() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s10-pipe-") as td:
        d = Path(td)
        logs = d / "logs.jsonl"
        artifacts = d / "artifacts"
        _seed_log(logs)

        _run(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(logs),
                "--output-root",
                str(artifacts),
                "--skip-fill-outcomes",
                "--train-ponder",
                "--no-train-hybrid",
                "--ponder-label-mode",
                "runtime_first",
                "--min-ponder-label-confidence",
                "0.5",
            ],
            ROOT,
        )

        runs = sorted([p for p in artifacts.iterdir() if p.is_dir()])
        assert runs
        latest = runs[-1]
        summary_path = latest / "summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for key in [
            "run_id",
            "started_at",
            "finished_at",
            "duration_sec",
            "input_logs_count",
            "records_raw",
            "records_labeled",
            "records_training",
            "ponder_training_run",
            "hybrid_training_run",
            "ponder_weights_path",
            "hybrid_weights_path",
            "quality_report_path",
            "errors",
            "warnings",
        ]:
            assert key in summary
        assert summary["records_raw"] >= 1
        assert summary["records_training"] >= 1
        assert Path(summary["ponder_weights_path"]).exists()


if __name__ == "__main__":
    test_learning_pipeline_generates_summary_and_artifacts()
    print("ok test_learning_pipeline_phase10")
