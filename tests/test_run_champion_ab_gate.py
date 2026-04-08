from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FAKE_SELFPLAY = """#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output-root", required=True)
parser.add_argument("--run-id", required=True)
parser.add_argument("--wrapper-options", default="")
args, _ = parser.parse_known_args()

run_dir = Path(args.output_root) / args.run_id
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "wrapper_logs").mkdir(parents=True, exist_ok=True)

def write_logs(games, records):
    with (run_dir / "games.jsonl").open("w", encoding="utf-8") as fh:
        for rec in games:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    with (run_dir / "wrapper_logs" / "taso-swindle-0001.jsonl").open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")
    (run_dir / "summary.json").write_text(json.dumps({"ok": True}), encoding="utf-8")

if args.run_id == "ab_old":
    write_logs(
        [
            {"winner": "black", "wrapper_is_black": True, "opening_moves": ["7g7f", "3c3d"]},
            {"winner": "white", "wrapper_is_black": True, "opening_moves": ["2g2f", "8c8d"]},
        ],
        [
            {"actual_move_in_reply_topk": True, "actual_move_rank_in_reply_topk": 1, "events": []},
            {"actual_move_in_reply_topk": False, "actual_move_rank_in_reply_topk": None, "events": ["verify_error"]},
        ],
    )
else:
    write_logs(
        [
            {"winner": "black", "wrapper_is_black": True, "opening_moves": ["7g7f", "3c3d"]},
            {"winner": "black", "wrapper_is_black": True, "opening_moves": ["2g2f", "8c8d"]},
        ],
        [
            {"actual_move_in_reply_topk": True, "actual_move_rank_in_reply_topk": 1, "events": []},
            {"actual_move_in_reply_topk": True, "actual_move_rank_in_reply_topk": 2, "events": []},
        ],
    )
"""


def _run(cmd: list[str]) -> None:
    done = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode != 0:
        raise AssertionError(f"rc={done.returncode}\\nSTDOUT:\\n{done.stdout}\\nSTDERR:\\n{done.stderr}")


def test_run_champion_ab_gate_generates_report_and_adopts() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-champion-") as td:
        d = Path(td)
        current = d / "current_hybrid.json"
        candidate = d / "candidate_hybrid.json"
        models_hybrid = d / "models_hybrid.json"
        snapshots = d / "snapshots"
        opening = d / "openings.txt"
        selfplay = d / "fake_selfplay.py"
        output = d / "out"

        current.write_text('{"w": 1}', encoding="utf-8")
        candidate.write_text('{"w": 2}', encoding="utf-8")
        models_hybrid.write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
        opening.write_text("7g7f 3c3d\n", encoding="utf-8")
        selfplay.write_text(FAKE_SELFPLAY, encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/run_champion_ab_gate.py",
                "--current-hybrid",
                str(current),
                "--candidate-hybrid",
                str(candidate),
                "--models-hybrid-path",
                str(models_hybrid),
                "--snapshot-dir",
                str(snapshots),
                "--opening-file",
                str(opening),
                "--selfplay-script",
                str(selfplay),
                "--output-root",
                str(output),
                "--run-id",
                "case1",
                "--games",
                "2",
                "--nodes",
                "10",
                "--max-plies",
                "32",
                "--seed",
                "1",
                "--gate-min-winrate-delta",
                "0.01",
                "--gate-min-topk-delta",
                "-0.01",
                "--gate-max-verify-error-delta",
                "1",
            ]
        )

        report = json.loads((output / "case1" / "reports" / "champion_ab_report.json").read_text(encoding="utf-8"))
        assert report["candidate_pass"] is True
        assert report["adopted"] is True
        assert report["winner"] == "candidate"
        assert models_hybrid.read_text(encoding="utf-8") == candidate.read_text(encoding="utf-8")
        assert Path(report["snapshot_path"]).exists()
        assert (output / "case1" / "reports" / "champion_ab_summary.md").exists()


def test_run_champion_ab_gate_reject_keeps_current_model() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-champion-reject-") as td:
        d = Path(td)
        current = d / "current_hybrid.json"
        candidate = d / "candidate_hybrid.json"
        models_hybrid = d / "models_hybrid.json"
        snapshots = d / "snapshots"
        opening = d / "openings.txt"
        selfplay = d / "fake_selfplay.py"
        output = d / "out"

        current.write_text('{"w": 1}', encoding="utf-8")
        candidate.write_text('{"w": 2}', encoding="utf-8")
        models_hybrid.write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
        opening.write_text("7g7f 3c3d\n", encoding="utf-8")
        selfplay.write_text(FAKE_SELFPLAY, encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/run_champion_ab_gate.py",
                "--current-hybrid",
                str(current),
                "--candidate-hybrid",
                str(candidate),
                "--models-hybrid-path",
                str(models_hybrid),
                "--snapshot-dir",
                str(snapshots),
                "--opening-file",
                str(opening),
                "--selfplay-script",
                str(selfplay),
                "--output-root",
                str(output),
                "--run-id",
                "case2",
                "--games",
                "2",
                "--nodes",
                "10",
                "--max-plies",
                "32",
                "--seed",
                "1",
                "--gate-min-winrate-delta",
                "0.90",
                "--gate-min-topk-delta",
                "0.50",
                "--gate-max-verify-error-delta",
                "0",
            ]
        )

        report = json.loads((output / "case2" / "reports" / "champion_ab_report.json").read_text(encoding="utf-8"))
        assert report["candidate_pass"] is False
        assert report["adopted"] is False
        assert report["winner"] == "current"
        assert models_hybrid.read_text(encoding="utf-8") == current.read_text(encoding="utf-8")


if __name__ == "__main__":
    test_run_champion_ab_gate_generates_report_and_adopts()
    test_run_champion_ab_gate_reject_keeps_current_model()
    print("ok test_run_champion_ab_gate")
