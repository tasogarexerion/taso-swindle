#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.swindle.weight_tuner import PONDER_GATE_FEATURES_VERSION


DEFAULT_WEIGHTS = {
    "bias": 0.0,
    "reply_coverage": 0.06,
    "candidate_count": 0.03,
    "top_gap12": 0.02,
    "had_mate_signal": -0.04,
    "elapsed_ms": 0.01,
    "cache_age_ms": -0.04,
    "verify_done_for_mate_cache": 0.03,
    "reuse_then_bestmove_changed": -0.08,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export default ponder gate learned adjustment weights JSON.")
    parser.add_argument("--output", default="", help="output JSON path")
    parser.add_argument("--output-dir", default="", help="output directory (default file: ponder_gate_weights.json)")
    parser.add_argument("--summary-out", default="", help="optional summary JSON path")
    args = parser.parse_args()

    out_path = _resolve_output_path(
        output=args.output,
        output_dir=args.output_dir,
        filename="ponder_gate_weights.json",
    )
    payload = {
        "version": 1,
        "kind": "ponder_gate_adjustment",
        "source": "default_seed",
        "label_mode": "heuristic",
        "trained_samples": 0,
        "features_version": PONDER_GATE_FEATURES_VERSION,
        "threshold_suggested": 0.55,
        "weights": DEFAULT_WEIGHTS,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _write_summary(
        args.summary_out,
        {
            "output": str(out_path),
            "kind": payload["kind"],
            "label_mode": payload["label_mode"],
            "features_version": payload["features_version"],
        },
    )
    print(f"wrote {out_path}")
    return 0


def _resolve_output_path(*, output: str, output_dir: str, filename: str) -> Path:
    out = (output or "").strip()
    if out:
        return Path(out)
    out_dir = (output_dir or "").strip()
    if out_dir:
        return Path(out_dir) / filename
    raise SystemExit("either --output or --output-dir is required")


def _write_summary(path: str, payload: dict[str, Any]) -> None:
    raw = (path or "").strip()
    if not raw:
        return
    out = Path(raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
