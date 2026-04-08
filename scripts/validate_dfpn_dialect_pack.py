#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.dfpn_adapter import validate_dialect_pack_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate external df-pn dialect pack JSON")
    parser.add_argument("--pack", required=True, help="path to dialect pack JSON")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON summary")
    args = parser.parse_args()

    report = validate_dialect_pack_file(args.pack)

    summary = {
        "pack": report.path,
        "version": report.version,
        "valid_count": len(report.valid_pack_names),
        "invalid_count": len(report.invalid_pack_names),
        "error_count": len(report.errors),
        "valid_packs": list(report.valid_pack_names),
        "invalid_packs": list(report.invalid_pack_names),
        "errors": list(report.errors),
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"pack={summary['pack']}")
        print(f"version={summary['version']}")
        print(
            f"valid={summary['valid_count']} invalid={summary['invalid_count']} errors={summary['error_count']}"
        )
        if report.valid_pack_names:
            print("valid_packs:")
            for name in report.valid_pack_names:
                print(f" - {name}")
        if report.invalid_pack_names:
            print("invalid_packs:")
            for name in report.invalid_pack_names:
                print(f" - {name}")
        if report.errors:
            print("errors:")
            for err in report.errors:
                print(f" - {err}")

    if report.valid_pack_names and not report.invalid_pack_names and not report.errors:
        code = 0
    elif report.valid_pack_names:
        code = 1
    else:
        code = 2
    print(f"summary_exit_code={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
