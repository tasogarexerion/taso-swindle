from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _seed_event(game_id: str) -> dict:
    return {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": game_id,
        "ply": 5,
        "search_id": 12,
        "root_eval_cp": -600,
        "final_bestmove": "7g7f",
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "unknown",
        "candidates": [{"move": "7g7f", "base_cp": -220, "reply_topk": [{"move": "3c3d"}, {"move": "8c8d"}]}],
        "actual_opponent_move": "8c8d",
        "actual_move_in_reply_topk": True,
        "actual_move_rank_in_reply_topk": 2,
        "outcome_tag": "swing_success",
        "outcome_confidence": 0.8,
        "outcome_match_source": "game_id_exact",
        "outcome_match_confidence": 0.9,
        "outcome_match_candidates": 1,
        "ponder_cache_hit": True,
        "ponder_cache_used": True,
        "ponder_label_source": "runtime_observed",
        "ponder_label_confidence": 0.9,
        "ponder_reuse_target": 1.0,
    }


def _run_pipeline(args: list[str], ok_codes: set[int] | None = None) -> dict:
    done = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True)  # noqa: S603,S607
    allowed = ok_codes if ok_codes is not None else {0}
    if done.returncode not in allowed:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")
    lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_resume_hash_detects_input_change() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s13-hash-") as td:
        d = Path(td)
        logs = d / "logs.jsonl"
        artifacts = d / "artifacts"
        logs.write_text(json.dumps(_seed_event("g1")) + "\n", encoding="utf-8")

        _run_pipeline(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(logs),
                "--output-root",
                str(artifacts),
                "--skip-fill-outcomes",
                "--no-train-ponder",
                "--no-train-hybrid",
            ]
        )

        second = _run_pipeline(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(logs),
                "--output-root",
                str(artifacts),
                "--skip-fill-outcomes",
                "--no-train-ponder",
                "--no-train-hybrid",
                "--resume",
            ]
        )
        assert "merge_logs" in second["skipped_stages"]

        with logs.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_seed_event("g2")) + "\n")

        third = _run_pipeline(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(logs),
                "--output-root",
                str(artifacts),
                "--skip-fill-outcomes",
                "--no-train-ponder",
                "--no-train-hybrid",
                "--resume",
            ]
        )
        assert "merge_logs" in third["executed_stages"]
        assert "merge_logs" in third["stage_hash_mismatch_stages"]


if __name__ == "__main__":
    test_resume_hash_detects_input_change()
    print("ok test_learning_pipeline_hash_diff_phase13")
