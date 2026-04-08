#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_WEIGHTS = {
    "bias": 0.0,
    "agree": 0.04,
    "conflict": -0.06,
    "verifier_for_us": 0.02,
    "verifier_for_them": -0.03,
    "dfpn_for_us": 0.02,
    "dfpn_for_them": -0.04,
    "verifier_conf": 0.03,
    "dfpn_conf": 0.03,
    "distance_available": 0.01,
    "strict_hit": 0.02,
    "loose_hit": 0.01,
    "actual_in_topk": -0.02,
    "actual_rank_inv": -0.01,
    "outcome_win": 0.03,
    "outcome_loss": -0.03,
    "outcome_draw": 0.0,
    "mode_top": 0.005,
    "mode_aggressive": 0.003,
    "parser_strict": 0.008,
    "parser_loose": 0.004,
    "emergency": -0.02,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Export default hybrid adjustment weights JSON.")
    parser.add_argument("--output", default="", help="output JSON path")
    parser.add_argument("--output-dir", default="", help="output directory (default file: hybrid_weights.json)")
    parser.add_argument("--summary-out", default="", help="optional summary JSON path")
    args = parser.parse_args()

    out_path = _resolve_output_path(
        output=args.output,
        output_dir=args.output_dir,
        filename="hybrid_weights.json",
    )
    payload = {
        "version": 2,
        "kind": "hybrid_adjustment",
        "source": "default_seed",
        "label_mode": "pseudo",
        "trained_samples": 0,
        "features_version": "v2",
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
