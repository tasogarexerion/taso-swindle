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
    if done.returncode != 0:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")


def _event(*, runtime_target: float | None, runtime_source: str, runtime_conf: float) -> dict:
    return {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": "g1",
        "ply": 5,
        "search_id": 11,
        "final_bestmove": "2g2f",
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "unknown",
        "candidates": [{"move": "2g2f", "reply_topk": [{"move": "3c3d"}, {"move": "8c8d"}]}],
        "ponder_cache_hit": True,
        "ponder_cache_used": True,
        "ponder_status_summary": "ok",
        "ponder_cache_gate_reason": None,
        "reuse_then_bestmove_changed": True,
        "ponder_reuse_target": runtime_target,
        "ponder_label_source": runtime_source,
        "ponder_label_confidence": runtime_conf,
        "actual_opponent_move": "8c8d",
        "actual_move_in_reply_topk": True,
        "actual_move_rank_in_reply_topk": 2,
        "outcome_tag": "swing_success",
        "outcome_confidence": 0.8,
    }


def test_build_labels_runtime_first_prefers_runtime() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s9-build-") as td:
        d = Path(td)
        inp = d / "in.jsonl"
        out = d / "labels.jsonl"
        inp.write_text(json.dumps(_event(runtime_target=0.0, runtime_source="runtime_observed", runtime_conf=0.95)) + "\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/build_training_labels.py",
                "--input",
                str(inp),
                "--output",
                str(out),
                "--label-mode",
                "mixed",
                "--ponder-label-mode",
                "runtime_first",
            ],
            ROOT,
        )
        rec = json.loads(out.read_text(encoding="utf-8").strip())
        assert rec["ponder_reuse_target"] == 0.0
        assert rec["ponder_label_source"] == "runtime_observed"
        assert rec["ponder_label_confidence"] >= 0.9


def test_build_labels_min_ponder_label_confidence_filters() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s9-build-") as td:
        d = Path(td)
        inp = d / "in.jsonl"
        out = d / "labels.jsonl"
        inp.write_text(json.dumps(_event(runtime_target=1.0, runtime_source="runtime_observed", runtime_conf=0.2)) + "\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/build_training_labels.py",
                "--input",
                str(inp),
                "--output",
                str(out),
                "--label-mode",
                "mixed",
                "--ponder-label-mode",
                "runtime_first",
                "--min-ponder-label-confidence",
                "0.5",
            ],
            ROOT,
        )
        rec = json.loads(out.read_text(encoding="utf-8").strip())
        assert rec["ponder_reuse_target"] is None


if __name__ == "__main__":
    test_build_labels_runtime_first_prefers_runtime()
    test_build_labels_min_ponder_label_confidence_filters()
    print("ok test_build_training_labels_ponder_phase9")
