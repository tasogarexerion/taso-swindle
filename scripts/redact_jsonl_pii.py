#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any

from project_policies import anonymize_learning_sample

PII_FIELDS = ("game_id", "game_id_raw", "game_id_normalized", "source_log_path", "_source_log_path")


def _resolve_inputs(raw_inputs: list[str], raw_globs: list[str]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()

    for raw in raw_inputs:
        p = Path(raw).expanduser()
        if p.exists() and p.is_file():
            rp = str(p.resolve())
            if rp not in seen:
                out.append(p)
                seen.add(rp)

    for pattern in raw_globs:
        for hit in sorted(glob.glob(pattern, recursive=True)):
            p = Path(hit).expanduser()
            if not p.exists() or not p.is_file():
                continue
            rp = str(p.resolve())
            if rp in seen:
                continue
            out.append(p)
            seen.add(rp)
    return out


def _common_base(paths: list[Path]) -> Path:
    if not paths:
        return Path(".")
    resolved = [str(p.resolve()) for p in paths]
    base = Path(resolved[0]).parent
    try:
        common = Path(Path(*Path(resolved[0]).parts[:1]).anchor + "/")
        # `os.path.commonpath` handles path-style safely.
        import os

        common = Path(os.path.commonpath(resolved))
        if common.is_file():
            common = common.parent
        base = common
    except Exception:
        pass
    return base


def _output_path(src: Path, *, output_dir: Path, base: Path, idx: int) -> Path:
    try:
        rel = src.resolve().relative_to(base.resolve())
        return output_dir / rel
    except Exception:
        return output_dir / f"{idx:06d}_{src.name}"


def _redact_file(src: Path, dst: Path, *, salt: str) -> dict[str, Any]:
    records_total = 0
    records_redacted = 0
    parse_errors = 0
    unchanged = 0
    replaced_fields = 0

    dst.parent.mkdir(parents=True, exist_ok=True)
    with src.open("r", encoding="utf-8") as fin, dst.open("w", encoding="utf-8") as fout:
        for raw in fin:
            line = raw.rstrip("\n")
            if not line.strip():
                fout.write("\n")
                continue
            try:
                rec = json.loads(line)
            except Exception:
                parse_errors += 1
                fout.write(line + "\n")
                continue
            if not isinstance(rec, dict):
                parse_errors += 1
                fout.write(line + "\n")
                continue

            records_total += 1
            before = {k: rec.get(k) for k in PII_FIELDS}
            before_author = None
            if isinstance(rec.get("backend_engine_info"), dict):
                before_author = rec["backend_engine_info"].get("author")
            redacted = anonymize_learning_sample(rec, salt=salt, enabled=True)
            after = {k: redacted.get(k) for k in PII_FIELDS}
            diff = sum(1 for k in PII_FIELDS if before.get(k) != after.get(k))
            after_author = None
            if isinstance(redacted.get("backend_engine_info"), dict):
                after_author = redacted["backend_engine_info"].get("author")
            if before_author != after_author:
                diff += 1
            if diff > 0:
                records_redacted += 1
                replaced_fields += diff
            else:
                unchanged += 1
            fout.write(json.dumps(redacted, ensure_ascii=False) + "\n")

    return {
        "source": str(src),
        "output": str(dst),
        "records_total": records_total,
        "records_redacted": records_redacted,
        "records_unchanged": unchanged,
        "replaced_fields": replaced_fields,
        "parse_errors": parse_errors,
        "ok": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Redact PII-like fields from JSONL files.")
    parser.add_argument("--input", action="append", default=[], help="input JSONL file path (repeatable)")
    parser.add_argument("--input-glob", action="append", default=[], help="input glob pattern (repeatable)")
    parser.add_argument("--in-place", action="store_true", help="rewrite files in place")
    parser.add_argument("--output-dir", default="", help="output dir when not using --in-place")
    parser.add_argument("--salt", default="", help="optional anonymization salt")
    parser.add_argument("--summary-out", default="", help="summary JSON output path")
    args = parser.parse_args()

    files = _resolve_inputs(list(args.input), list(args.input_glob))
    if not files:
        raise SystemExit("no input files matched")

    if not args.in_place and not str(args.output_dir).strip():
        raise SystemExit("either --in-place or --output-dir is required")

    base = _common_base(files)
    output_dir = Path(args.output_dir).expanduser() if str(args.output_dir).strip() else Path(".")

    results: list[dict[str, Any]] = []
    failed = 0
    for idx, src in enumerate(files, start=1):
        if args.in_place:
            tmp = src.with_suffix(src.suffix + ".redact_tmp")
            try:
                info = _redact_file(src, tmp, salt=str(args.salt))
                tmp.replace(src)
                info["output"] = str(src)
                results.append(info)
            except Exception as exc:
                failed += 1
                try:
                    if tmp.exists():
                        tmp.unlink()
                except Exception:
                    pass
                results.append(
                    {
                        "source": str(src),
                        "output": str(src),
                        "records_total": 0,
                        "records_redacted": 0,
                        "records_unchanged": 0,
                        "replaced_fields": 0,
                        "parse_errors": 0,
                        "ok": False,
                        "error": str(exc),
                    }
                )
        else:
            dst = _output_path(src, output_dir=output_dir, base=base, idx=idx)
            try:
                info = _redact_file(src, dst, salt=str(args.salt))
                results.append(info)
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "source": str(src),
                        "output": str(dst),
                        "records_total": 0,
                        "records_redacted": 0,
                        "records_unchanged": 0,
                        "replaced_fields": 0,
                        "parse_errors": 0,
                        "ok": False,
                        "error": str(exc),
                    }
                )

    summary = {
        "inputs": [str(p) for p in files],
        "in_place": bool(args.in_place),
        "output_dir": str(output_dir) if not args.in_place else "",
        "salt_used": bool(str(args.salt)),
        "processed_files": len(files),
        "failed_files": failed,
        "records_total": sum(int(x.get("records_total", 0)) for x in results if bool(x.get("ok", False))),
        "records_redacted": sum(int(x.get("records_redacted", 0)) for x in results if bool(x.get("ok", False))),
        "records_unchanged": sum(int(x.get("records_unchanged", 0)) for x in results if bool(x.get("ok", False))),
        "replaced_fields": sum(int(x.get("replaced_fields", 0)) for x in results if bool(x.get("ok", False))),
        "parse_errors": sum(int(x.get("parse_errors", 0)) for x in results if bool(x.get("ok", False))),
        "results": results,
    }

    raw_summary = str(args.summary_out or "").strip()
    if raw_summary:
        p = Path(raw_summary).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
