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


def _write_eval(path: Path, swing_success: int, swing_fail: int, neutral: int) -> None:
    rows = []
    for i in range(swing_success):
        rows.append({"outcome_tag": "swing_success", "outcome_confidence": 0.8, "actual_move_in_reply_topk": False, "reuse_then_bestmove_changed": False})
    for i in range(swing_fail):
        rows.append({"outcome_tag": "swing_fail", "outcome_confidence": 0.7, "actual_move_in_reply_topk": True, "reuse_then_bestmove_changed": True})
    for i in range(neutral):
        rows.append({"outcome_tag": "neutral", "outcome_confidence": 0.5, "actual_move_in_reply_topk": True, "reuse_then_bestmove_changed": False})
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_weights_ab_with_actual_game_eval_diff() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s13-ab-eval-") as td:
        d = Path(td)
        a = d / "a.json"
        b = d / "b.json"
        out = d / "report.json"
        eval_a = d / "eval_a.jsonl"
        eval_b = d / "eval_b.jsonl"

        a.write_text(json.dumps({"kind": "ponder_gate_adjustment", "features_version": "v1", "weights": {"bias": 0.0}}), encoding="utf-8")
        b.write_text(json.dumps({"kind": "ponder_gate_adjustment", "features_version": "v1", "weights": {"bias": 0.1}}), encoding="utf-8")
        _write_eval(eval_a, swing_success=2, swing_fail=5, neutral=3)
        _write_eval(eval_b, swing_success=6, swing_fail=2, neutral=2)

        _run(
            [
                sys.executable,
                "scripts/report_weights_ab.py",
                "--a",
                str(a),
                "--b",
                str(b),
                "--type",
                "ponder",
                "--out",
                str(out),
                "--eval-log-a",
                str(eval_a),
                "--eval-log-b",
                str(eval_b),
            ],
            ROOT,
        )

        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["actual_game_eval"]["available"] is True
        diff = rep["actual_game_eval"]["diff"]
        assert float(diff["winloss_balance"]) > 0.0
        assert "swing_success_rate" in diff


if __name__ == "__main__":
    test_weights_ab_with_actual_game_eval_diff()
    print("ok test_weights_ab_report_phase13")
