#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def _find_latest_run(artifacts_root: Path, require_summary_success: bool) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if not artifacts_root.exists():
        warnings.append(f"artifacts_root_not_found:{artifacts_root}")
        return None, warnings

    runs = [p for p in artifacts_root.iterdir() if p.is_dir()]
    runs.sort(key=lambda p: p.name, reverse=True)
    for run in runs:
        summary_path = run / "summary.json"
        if not summary_path.exists():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception:
            warnings.append(f"summary_parse_error:{summary_path}")
            continue
        if require_summary_success and summary.get("status") not in {"success", "partial"}:
            continue
        return run, warnings
    warnings.append("no_usable_run_found")
    return None, warnings


def _install(src: Path, dst: Path, dry_run: bool) -> dict[str, Any]:
    if not src.exists():
        return {"ok": False, "warning": f"source_missing:{src}", "src": str(src), "dst": str(dst)}
    if dry_run:
        return {"ok": True, "dry_run": True, "src": str(src), "dst": str(dst)}
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"ok": True, "dry_run": False, "src": str(src), "dst": str(dst)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Install latest learned weights from artifacts/learning_runs.")
    parser.add_argument("--artifacts-root", required=True, help="artifacts root path")
    parser.add_argument("--ponder-dst", required=True, help="destination path for ponder gate weights")
    parser.add_argument("--hybrid-dst", required=True, help="destination path for hybrid weights")
    parser.add_argument("--require-summary-success", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary-out", default="", help="optional summary JSON path")
    args = parser.parse_args()

    artifacts_root = Path(args.artifacts_root)
    run_dir, warnings = _find_latest_run(artifacts_root, bool(args.require_summary_success))
    result: dict[str, Any] = {
        "artifacts_root": str(artifacts_root),
        "selected_run": str(run_dir) if run_dir else None,
        "dry_run": bool(args.dry_run),
        "require_summary_success": bool(args.require_summary_success),
        "warnings": list(warnings),
        "ponder": None,
        "hybrid": None,
    }

    if run_dir is None:
        result["status"] = "error"
        _emit(result, args.summary_out)
        print("no run found")
        return 1

    ponder_src = run_dir / "weights" / "ponder_gate_weights.json"
    hybrid_src = run_dir / "weights" / "hybrid_weights.json"
    ponder_dst = Path(args.ponder_dst)
    hybrid_dst = Path(args.hybrid_dst)

    result["ponder"] = _install(ponder_src, ponder_dst, bool(args.dry_run))
    result["hybrid"] = _install(hybrid_src, hybrid_dst, bool(args.dry_run))
    result["status"] = "success" if (result["ponder"]["ok"] or result["hybrid"]["ok"]) else "partial"

    _emit(result, args.summary_out)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _emit(payload: dict[str, Any], summary_out: str) -> None:
    path = (summary_out or "").strip()
    if not path:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
