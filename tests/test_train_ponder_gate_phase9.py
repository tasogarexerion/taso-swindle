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


def _write_dataset(path: Path) -> None:
    rows = [
        {
            "ponder_reuse_target": 1.0,
            "ponder_label_source": "runtime_observed",
            "ponder_label_confidence": 0.95,
            "ponder_cache_used": True,
            "reuse_then_bestmove_changed": False,
            "ponder_status_summary": "ok",
            "verify_status_summary": "confirmed",
            "ponder_used_budget_ms": 180,
            "ponder_cache_age_ms": 200,
            "candidates": [{"gap12": 350.0, "reply_topk": [{"move": "3c3d"}, {"move": "8c8d"}]}],
        },
        {
            "ponder_reuse_target": None,
            "ponder_label_source": "heuristic",
            "ponder_label_confidence": 0.35,
            "ponder_cache_hit": True,
            "ponder_cache_used": False,
            "ponder_cache_gate_reason": "quality_gate",
            "reuse_then_bestmove_changed": True,
            "ponder_status_summary": "fallback",
            "verify_status_summary": "not_used",
            "ponder_used_budget_ms": 60,
            "ponder_cache_age_ms": 1800,
            "candidates": [{"gap12": 50.0, "reply_topk": [{"move": "3c3d"}]}],
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_train_ponder_gate_uses_source_weighting() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s9-train-") as td:
        d = Path(td)
        inp = d / "train.jsonl"
        out_mixed = d / "ponder_mixed.json"
        out_heur = d / "ponder_heur.json"
        _write_dataset(inp)

        _run(
            [
                sys.executable,
                "scripts/train_ponder_gate.py",
                "--input",
                str(inp),
                "--output",
                str(out_mixed),
                "--label-mode",
                "mixed",
            ],
            ROOT,
        )
        _run(
            [
                sys.executable,
                "scripts/train_ponder_gate.py",
                "--input",
                str(inp),
                "--output",
                str(out_heur),
                "--label-mode",
                "heuristic",
            ],
            ROOT,
        )

        mixed = json.loads(out_mixed.read_text(encoding="utf-8"))
        heur = json.loads(out_heur.read_text(encoding="utf-8"))
        assert mixed["trained_samples"] >= heur["trained_samples"]
        assert mixed["runtime_samples"] >= 1
        assert mixed["weights"] != heur["weights"]


def test_train_ponder_gate_metadata_written() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s9-train-") as td:
        d = Path(td)
        inp = d / "train.jsonl"
        out_path = d / "ponder.json"
        _write_dataset(inp)

        _run(
            [
                sys.executable,
                "scripts/train_ponder_gate.py",
                "--input",
                str(inp),
                "--output",
                str(out_path),
                "--label-mode",
                "mixed",
            ],
            ROOT,
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["kind"] == "ponder_gate_adjustment"
        assert "label_mode" in payload
        assert "runtime_label_ratio" in payload
        assert "heuristic_label_ratio" in payload
        assert "avg_label_confidence" in payload


if __name__ == "__main__":
    test_train_ponder_gate_uses_source_weighting()
    test_train_ponder_gate_metadata_written()
    print("ok test_train_ponder_gate_phase9")
