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


def test_learning_quality_report_counts() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s10-quality-") as td:
        d = Path(td)
        inp = d / "logs.jsonl"
        out_json = d / "report.json"
        out_txt = d / "report.txt"
        rows = [
            {
                "ponder_label_source": "runtime_observed",
                "ponder_label_confidence": 0.9,
                "reuse_then_bestmove_changed": False,
                "actual_opponent_move": "3c3d",
                "outcome_tag": "swing_success",
                "dfpn_parse_unknown_count": 0,
                "ponder_reuse_target": 1.0,
                "label": 1.0,
                "outcome_confidence": 0.8,
            },
            {
                "ponder_label_source": "heuristic",
                "ponder_label_confidence": 0.3,
                "reuse_then_bestmove_changed": True,
                "actual_opponent_move": None,
                "outcome_tag": "unknown",
                "dfpn_parse_unknown_count": 1,
                "ponder_reuse_target": None,
                "label": None,
                "outcome_confidence": 0.2,
            },
        ]
        with inp.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        _run(
            [
                sys.executable,
                "scripts/report_learning_data_quality.py",
                "--input",
                str(inp),
                "--output-json",
                str(out_json),
                "--output-text",
                str(out_txt),
                "--min-ponder-label-confidence",
                "0.5",
                "--min-outcome-confidence",
                "0.5",
            ],
            ROOT,
        )

        rep = json.loads(out_json.read_text(encoding="utf-8"))
        assert rep["total_records"] == 2
        assert rep["ponder_label_source_counts"]["runtime_observed"] == 1
        assert rep["ponder_label_source_counts"]["heuristic"] == 1
        assert rep["eligible_for_ponder_training"] == 1
        assert rep["eligible_for_hybrid_training"] == 1
        assert rep["dfpn_parse_unknown_rate"] > 0.0
        assert out_txt.exists()


if __name__ == "__main__":
    test_learning_quality_report_counts()
    print("ok test_learning_quality_report_phase10")
