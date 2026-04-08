#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from dfpn_token_classifier import train_centroid_model


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return [dict(row) for row in reader]


def _resolve_output_path(*, output: str, output_dir: str, filename: str) -> Path:
    out = (output or "").strip()
    if out:
        return Path(out)
    out_dir = (output_dir or "").strip()
    if out_dir:
        return Path(out_dir) / filename
    raise SystemExit("either --output or --output-dir is required")


def main() -> int:
    parser = argparse.ArgumentParser(description="Train lightweight supervised classifier for df-pn candidate token classes.")
    parser.add_argument("--input", required=True, help="input features JSONL/CSV")
    parser.add_argument("--input-format", choices=["auto", "jsonl", "csv"], default="auto")
    parser.add_argument("--output", default="", help="output model JSON")
    parser.add_argument("--output-dir", default="", help="output dir (default file: dfpn_token_classifier.json)")
    parser.add_argument("--label-field", default="token_class_label", help="primary label field")
    parser.add_argument("--fallback-label-field", default="token_class", help="fallback label field")
    parser.add_argument("--min-samples", type=int, default=2)
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    fmt = args.input_format
    if fmt == "auto":
        fmt = "csv" if in_path.suffix.lower() == ".csv" else "jsonl"

    rows = _read_csv(in_path) if fmt == "csv" else _read_jsonl(in_path)
    model = train_centroid_model(
        rows,
        label_field=args.label_field,
        fallback_label_field=args.fallback_label_field,
        min_samples=max(1, int(args.min_samples)),
    )
    model["source_input"] = str(in_path)

    out_path = _resolve_output_path(
        output=args.output,
        output_dir=args.output_dir,
        filename="dfpn_token_classifier.json",
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} classes={len(model.get('classes', []))} samples={model.get('trained_samples', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
