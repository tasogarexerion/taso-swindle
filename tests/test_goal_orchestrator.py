from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


FAKE_CHAMPION = """#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import os
import shutil
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--current-hybrid", required=True)
parser.add_argument("--candidate-hybrid", required=True)
parser.add_argument("--models-hybrid-path", required=True)
parser.add_argument("--output-root", required=True)
parser.add_argument("--run-id", required=True)
parser.add_argument("--adopt", action=argparse.BooleanOptionalAction, default=True)
args, _ = parser.parse_known_args()

mode = os.environ.get("FAKE_CHAMPION_MODE", "always_non_adopt")
run_dir = Path(args.output_root) / args.run_id
reports = run_dir / "reports"
reports.mkdir(parents=True, exist_ok=True)
report_path = reports / "champion_ab_report.json"
summary_path = reports / "champion_ab_summary.md"

if mode == "stall_then_fail_retry":
    if "retry1" not in args.run_id:
        time.sleep(5.0)
        raise SystemExit(0)
    raise SystemExit(2)

if mode == "always_non_adopt":
    candidate_pass = False
elif mode == "main_pass_confirm_pass":
    candidate_pass = True
elif mode == "main_pass_confirm_fail":
    candidate_pass = "confirm" not in args.run_id
else:
    candidate_pass = False

adopted = bool(candidate_pass and args.adopt)
if adopted:
    Path(args.models_hybrid_path).parent.mkdir(parents=True, exist_ok=True)
    src = Path(args.candidate_hybrid).resolve()
    dst = Path(args.models_hybrid_path).resolve()
    if src != dst:
        shutil.copy2(src, dst)

failure_openings = run_dir / "failure_band" / "failure_openings.txt"
report = {
    "run_id": args.run_id,
    "status": "ok",
    "winner": "candidate" if candidate_pass else "current",
    "candidate_pass": bool(candidate_pass),
    "adopted": bool(adopted),
    "failure_openings_path": str(failure_openings),
    "delta": {"wrapper_win_rate": 0.0, "actual_move_topk_rate": 0.0, "verify_error_events": 0},
}
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
summary_path.write_text("# fake champion\\n", encoding="utf-8")
"""


FAKE_SELFPLAY = """#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--output-root", required=True)
parser.add_argument("--run-id", required=True)
parser.add_argument("--train-hybrid", action=argparse.BooleanOptionalAction, default=False)
args, _ = parser.parse_known_args()

run_dir = Path(args.output_root) / args.run_id
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "games_kif").mkdir(parents=True, exist_ok=True)

learning_root = run_dir / "learning_runs"
if args.train_hybrid:
    lr = learning_root / "20260101-000000" / "weights"
    lr.mkdir(parents=True, exist_ok=True)
    (lr / "hybrid_weights.json").write_text("{\\"w\\": 9}", encoding="utf-8")

summary = {"pipeline_output_root": str(learning_root)}
(run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
"""


FAKE_SMOKE = """#!/usr/bin/env python3
raise SystemExit(0)
"""


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _run_orchestrator(tmp: Path, mode: str, extra: list[str]) -> tuple[int, Path, dict[str, str]]:
    champion = tmp / "fake_champion.py"
    selfplay = tmp / "fake_selfplay.py"
    smoke = tmp / "fake_smoke.py"
    _write(champion, FAKE_CHAMPION)
    _write(selfplay, FAKE_SELFPLAY)
    _write(smoke, FAKE_SMOKE)

    current = tmp / "current.json"
    candidate = tmp / "candidate.json"
    current.write_text('{"w": 1}', encoding="utf-8")
    candidate.write_text('{"w": 2}', encoding="utf-8")

    out = tmp / "out"
    cmd = [
        sys.executable,
        "scripts/run_champion_goal_orchestrator.py",
        "--current-hybrid",
        str(current),
        "--seed-candidate-hybrid",
        str(candidate),
        "--output-root",
        str(out),
        "--run-id",
        "goal-test",
        "--champion-script",
        str(champion),
        "--selfplay-script",
        str(selfplay),
        "--smoke-script",
        str(smoke),
        "--backend-engine",
        "./YaneuraOu",
        "--backend-eval",
        "./eval",
        "--auto-smoke",
    ] + extra
    env = dict(os.environ)
    env["FAKE_CHAMPION_MODE"] = mode
    done = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, env=env)  # noqa: S603,S607
    return done.returncode, out / "goal-test", env


def test_goal_orchestrator_stop_on_non_adopt_streak() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-goal-streak-") as td:
        rc, run_dir, _ = _run_orchestrator(
            Path(td),
            "always_non_adopt",
            ["--max-cycles", "5", "--max-non-adopt-streak", "2", "--no-auto-smoke"],
        )
        assert rc == 0
        final = json.loads((run_dir / "final_decision.json").read_text(encoding="utf-8"))
        assert final["status"] == "completed_plateau"
        assert final["decision_reason"] == "max_non_adopt_streak_reached"
        assert int(final["cycles_total"]) == 2


def test_goal_orchestrator_stop_on_max_cycles() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-goal-maxc-") as td:
        rc, run_dir, _ = _run_orchestrator(
            Path(td),
            "always_non_adopt",
            ["--max-cycles", "2", "--max-non-adopt-streak", "10", "--no-auto-smoke"],
        )
        assert rc == 0
        final = json.loads((run_dir / "final_decision.json").read_text(encoding="utf-8"))
        assert final["status"] == "completed_plateau"
        assert final["decision_reason"] == "max_cycles_reached"
        assert int(final["cycles_total"]) == 2


def test_goal_orchestrator_confirm_pass_marks_final() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-goal-final-") as td:
        rc, run_dir, _ = _run_orchestrator(
            Path(td),
            "main_pass_confirm_pass",
            ["--max-cycles", "3", "--max-non-adopt-streak", "3", "--auto-smoke"],
        )
        assert rc == 0
        final = json.loads((run_dir / "final_decision.json").read_text(encoding="utf-8"))
        assert final["status"] == "final_champion_confirmed"
        assert final["decision_reason"] == "main_and_confirm_pass_with_smoke"
        assert final["smoke_ok"] is True


def test_goal_orchestrator_confirm_fail_rolls_back() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-goal-rollback-") as td:
        tmp = Path(td)
        rc, run_dir, _ = _run_orchestrator(
            tmp,
            "main_pass_confirm_fail",
            ["--max-cycles", "1", "--max-non-adopt-streak", "1", "--no-auto-smoke"],
        )
        assert rc == 0
        final = json.loads((run_dir / "final_decision.json").read_text(encoding="utf-8"))
        assert final["status"] == "completed_plateau"
        current = tmp / "current.json"
        # confirm fail path should rollback to original current
        assert current.read_text(encoding="utf-8") == '{"w": 1}'


def test_goal_orchestrator_stall_retry_then_fail() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-goal-stall-") as td:
        rc, run_dir, _ = _run_orchestrator(
            Path(td),
            "stall_then_fail_retry",
            [
                "--max-cycles",
                "1",
                "--max-non-adopt-streak",
                "1",
                "--stall-threshold-sec",
                "1",
                "--stall-poll-sec",
                "1",
                "--champion-timeout-sec",
                "3",
                "--no-auto-smoke",
            ],
        )
        assert rc == 1
        final = json.loads((run_dir / "final_decision.json").read_text(encoding="utf-8"))
        assert final["status"] == "failed"
        assert final["decision_reason"] == "main_ab_failed"


if __name__ == "__main__":
    test_goal_orchestrator_stop_on_non_adopt_streak()
    test_goal_orchestrator_stop_on_max_cycles()
    test_goal_orchestrator_confirm_pass_marks_final()
    test_goal_orchestrator_confirm_fail_rolls_back()
    test_goal_orchestrator_stall_retry_then_fail()
    print("ok test_goal_orchestrator")
