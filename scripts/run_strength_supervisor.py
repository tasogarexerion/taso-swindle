#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _json_load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, rec: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _build_default_autopilot_cmd(root: Path) -> list[str]:
    return [
        "python3",
        "scripts/run_strength_autopilot.py",
        "--output-root",
        "artifacts_local/strength_autopilot",
        "--fixed-openings",
        "artifacts_local/benchmarks/fixed_bench_openings_v1.txt",
        "--train-games",
        "180",
        "--ab-games",
        "300",
        "--nodes",
        "800",
        "--max-plies",
        "140",
        "--opening-plies",
        "10",
        "--seed",
        "20260227",
        "--train-hybrid",
        "--no-train-ponder",
        "--gate-min-winrate-delta",
        "0.02",
        "--gate-min-topk-delta",
        "-0.01",
        "--gate-max-verify-error-delta",
        "1",
        "--train-timeout-sec",
        "7200",
        "--ab-timeout-sec",
        "7200",
        "--heartbeat-interval-sec",
        "10",
        "--interval-sec",
        "300",
        "--max-cycles",
        "0",
        "--stop-file",
        "artifacts_local/strength_autopilot/STOP",
    ]


def _heartbeat_age_sec(state_path: Path) -> Optional[float]:
    state = _json_load(state_path)
    ts = str(state.get("heartbeat_at", "")).strip() or str(state.get("updated_at", "")).strip()
    if not ts:
        return None
    dt = _parse_iso(ts)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def main() -> int:
    parser = argparse.ArgumentParser(description="Supervisor for run_strength_autopilot.py with auto-restart and stale-heartbeat recovery.")
    parser.add_argument("--autopilot-cmd", default="", help="single command string; if empty, use medium-load default")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--state-file", default="artifacts_local/strength_autopilot/supervisor_state.json")
    parser.add_argument("--history-file", default="artifacts_local/strength_autopilot/supervisor_history.jsonl")
    parser.add_argument("--stop-file", default="artifacts_local/strength_autopilot/SUPERVISOR_STOP")
    parser.add_argument("--autopilot-state-file", default="artifacts_local/strength_autopilot/state.json")
    parser.add_argument("--poll-sec", type=int, default=10)
    parser.add_argument("--restart-delay-sec", type=int, default=20)
    parser.add_argument("--stale-heartbeat-sec", type=int, default=1800)
    parser.add_argument("--startup-grace-sec", type=int, default=90)
    parser.add_argument("--max-restarts", type=int, default=0, help="0 means unlimited")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    cwd = Path(args.cwd).expanduser() if args.cwd else root
    if not cwd.is_absolute():
        cwd = (root / cwd).resolve()

    state_path = Path(args.state_file).expanduser()
    if not state_path.is_absolute():
        state_path = (root / state_path).resolve()
    history_path = Path(args.history_file).expanduser()
    if not history_path.is_absolute():
        history_path = (root / history_path).resolve()
    stop_file = Path(args.stop_file).expanduser()
    if not stop_file.is_absolute():
        stop_file = (root / stop_file).resolve()
    autopilot_state_path = Path(args.autopilot_state_file).expanduser()
    if not autopilot_state_path.is_absolute():
        autopilot_state_path = (root / autopilot_state_path).resolve()

    cmd = shlex.split(args.autopilot_cmd) if args.autopilot_cmd.strip() else _build_default_autopilot_cmd(root)
    if not cmd:
        raise SystemExit("autopilot command is empty")

    restarts = 0
    _json_dump(
        state_path,
        {
            "started_at": _now_iso(),
            "status": "starting",
            "cwd": str(cwd),
            "cmd": cmd,
            "restarts": 0,
            "last_exit_code": None,
            "last_exit_reason": "",
            "updated_at": _now_iso(),
        },
    )

    while True:
        if stop_file.exists():
            print(f"[supervisor] stop file detected: {stop_file}")
            break

        print(f"[supervisor] launch: {' '.join(shlex.quote(x) for x in cmd)}")
        proc = subprocess.Popen(cmd, cwd=str(cwd), text=True)  # noqa: S603,S607
        launched_at = _now_iso()
        launched_wall = time.time()
        exit_reason = "process_exit"

        while True:
            if stop_file.exists():
                exit_reason = "supervisor_stop"
                try:
                    proc.terminate()
                    proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                break

            rc = proc.poll()
            if rc is not None:
                break

            stale_sec = int(args.stale_heartbeat_sec)
            grace_sec = max(0, int(args.startup_grace_sec))
            if stale_sec > 0 and (time.time() - launched_wall) >= grace_sec and autopilot_state_path.exists():
                age = _heartbeat_age_sec(autopilot_state_path)
                if age is not None and age > stale_sec:
                    exit_reason = f"stale_heartbeat:{int(age)}"
                    print(f"[supervisor] stale heartbeat age={age:.1f}s -> restart")
                    try:
                        proc.terminate()
                        proc.wait(timeout=8)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break

            _json_dump(
                state_path,
                {
                    "started_at": _json_load(state_path).get("started_at", _now_iso()),
                    "status": "running",
                    "cwd": str(cwd),
                    "cmd": cmd,
                    "child_pid": proc.pid,
                    "launched_at": launched_at,
                    "restarts": restarts,
                    "last_exit_code": None,
                    "last_exit_reason": "",
                    "updated_at": _now_iso(),
                },
            )
            time.sleep(max(2, int(args.poll_sec)))

        rc = proc.poll()
        rc_int = int(rc) if rc is not None else -1
        _append_jsonl(
            history_path,
            {
                "ts": _now_iso(),
                "event": "child_exit",
                "exit_code": rc_int,
                "exit_reason": exit_reason,
                "restarts": restarts,
                "cmd": cmd,
            },
        )
        _json_dump(
            state_path,
            {
                "started_at": _json_load(state_path).get("started_at", _now_iso()),
                "status": "child_exited",
                "cwd": str(cwd),
                "cmd": cmd,
                "restarts": restarts,
                "last_exit_code": rc_int,
                "last_exit_reason": exit_reason,
                "updated_at": _now_iso(),
            },
        )

        if stop_file.exists():
            break
        if int(args.max_restarts) > 0 and restarts >= int(args.max_restarts):
            print("[supervisor] max restarts reached")
            break

        restarts += 1
        print(f"[supervisor] restart in {int(args.restart_delay_sec)} sec")
        time.sleep(max(1, int(args.restart_delay_sec)))

    _json_dump(
        state_path,
        {
            "started_at": _json_load(state_path).get("started_at", _now_iso()),
            "status": "stopped",
            "cwd": str(cwd),
            "cmd": cmd,
            "restarts": restarts,
            "updated_at": _now_iso(),
        },
    )
    print("[supervisor] finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
