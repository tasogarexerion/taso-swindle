from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path, ok_codes: set[int] | None = None) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    allowed = ok_codes if ok_codes is not None else {0}
    if done.returncode not in allowed:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")
    return done


def _seed_log(path: Path, *, game_id: str) -> None:
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


def _latest_summary(artifacts: Path) -> dict:
    runs = sorted([p for p in artifacts.iterdir() if p.is_dir()])
    assert runs
    summary_path = runs[-1] / "summary.json"
    assert summary_path.exists()
    return json.loads(summary_path.read_text(encoding="utf-8"))


def test_multilog_summary_has_input_sources() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-multilog-") as td:
        d = Path(td)
        artifacts = d / "artifacts"
        log_a = d / "a.jsonl"
        log_b = d / "b.jsonl"
        _seed_log(log_a, game_id="ga")
        _seed_log(log_b, game_id="gb")

        _run(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(log_a),
                str(log_b),
                "--output-root",
                str(artifacts),
                "--skip-fill-outcomes",
                "--no-train-ponder",
                "--no-train-hybrid",
            ],
            ROOT,
        )

        summary = _latest_summary(artifacts)
        assert "input_sources" in summary
        assert isinstance(summary["input_sources"], list)
        assert len(summary["input_sources"]) >= 2
        paths = {str(item.get("path")): item for item in summary["input_sources"]}
        assert str(log_a) in paths
        assert str(log_b) in paths
        assert int(paths[str(log_a)].get("record_count", 0)) == 1
        assert int(paths[str(log_b)].get("record_count", 0)) == 1
        assert int(paths[str(log_a)].get("labeled_count", 0)) >= 1
        assert int(paths[str(log_b)].get("training_count", 0)) >= 1


def test_failed_stage_and_partial_outputs_on_fill_failure() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-failstage-") as td:
        d = Path(td)
        artifacts = d / "artifacts"
        log_a = d / "a.jsonl"
        _seed_log(log_a, game_id="ga")
        missing_kif_dir = d / "no_kif_here"

        _run(
            [
                sys.executable,
                "scripts/run_learning_pipeline.py",
                "--logs",
                str(log_a),
                "--kif-dir",
                str(missing_kif_dir),
                "--output-root",
                str(artifacts),
                "--no-train-ponder",
                "--no-train-hybrid",
            ],
            ROOT,
            ok_codes={1},
        )

        summary = _latest_summary(artifacts)
        assert summary.get("failed_stage") == "fill_kif"
        partial = summary.get("partial_outputs")
        assert isinstance(partial, list)
        assert any("merged_raw.jsonl" in str(x) for x in partial)


if __name__ == "__main__":
    test_multilog_summary_has_input_sources()
    test_failed_stage_and_partial_outputs_on_fill_failure()
    print("ok test_learning_pipeline_multilog_phase11")
