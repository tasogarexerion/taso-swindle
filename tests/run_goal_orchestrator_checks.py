#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(label: str, cmd: list[str]) -> None:
    print(f"[run_goal_orchestrator_checks] {label}: {' '.join(cmd)}")
    done = subprocess.run(cmd, cwd=str(ROOT), text=True)  # noqa: S603,S607
    if done.returncode != 0:
        raise SystemExit(done.returncode)


def main() -> int:
    _run("goal_orchestrator_tests", [sys.executable, "tests/test_goal_orchestrator.py"])
    print("[run_goal_orchestrator_checks] all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

