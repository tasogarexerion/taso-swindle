from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _seed_log(path: Path, game_id: str = "g1") -> None:
    rec = {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": game_id,
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
        "candidates": [{"move": "7g7f", "base_cp": -220, "reply_topk": [{"move": "3c3d"}, {"move": "8c8d"}]}],
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
        "outcome_match_source": "game_id_exact",
        "outcome_match_confidence": 0.9,
        "outcome_match_candidates": 1,
    }
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_pipeline(cmd: list[str], ok_codes: set[int] | None = None) -> dict:
    done = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)  # noqa: S603,S607
    allowed = ok_codes if ok_codes is not None else {0}
    if done.returncode not in allowed:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")
    lines = [ln.strip() for ln in done.stdout.splitlines() if ln.strip()]
    assert lines
    payload = json.loads(lines[-1])
    assert isinstance(payload, dict)
    return payload


def test_resume_and_force_stage() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s12-resume-") as td:
        d = Path(td)
        logs = d / "logs.jsonl"
        artifacts = d / "artifacts"
        _seed_log(logs)

        first = _run_pipeline(
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
        assert first["status"] == "success"

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
        assert second["resume_used"] is True
        assert "build_labels" in second["skipped_stages"]

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
                "--force-stage",
                "build_labels",
            ]
        )
        assert "build_labels" in third["executed_stages"]


def test_retry_records_stage_attempts() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s12-retry-") as td:
        d = Path(td)
        logs = d / "logs.jsonl"
        artifacts = d / "artifacts"
        _seed_log(logs)
        missing_kif_dir = d / "missing_kif"

        result = _run_pipeline(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(logs),
                "--output-root",
                str(artifacts),
                "--kif-dir",
                str(missing_kif_dir),
                "--no-train-ponder",
                "--no-train-hybrid",
                "--retry",
                "1",
            ],
            ok_codes={1},
        )
        assert result["failed_stage"] == "fill_kif"
        assert int(result["retry_count_by_stage"].get("fill_kif", 0)) == 1
        assert "fill_kif" in result["executed_stages"]


if __name__ == "__main__":
    test_resume_and_force_stage()
    test_retry_records_stage_attempts()
    print("ok test_learning_pipeline_resume_phase12")
