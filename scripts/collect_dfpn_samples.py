#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.dfpn_adapter import DfPnAdapter, _parse_output  # type: ignore[attr-defined]


def _short(text: str, limit: int = 500) -> str:
    normalized = " ".join((text or "").splitlines())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit]


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect df-pn stdout/stderr samples into JSONL")
    parser.add_argument("--dfpn", required=True, help='df-pn command as a single string (example: "./dfpn")')
    parser.add_argument("--position", default="position startpos", help="position command payload")
    parser.add_argument("--move", default="7g7f", help="candidate move payload")
    parser.add_argument("--dialect-requested", default="AUTO", help="df-pn dialect hint (AUTO/GENERIC_EN/...)")
    parser.add_argument("--parser-mode", default="AUTO", choices=["AUTO", "STRICT", "LOOSE"], help="parser mode")
    parser.add_argument("--dialect-pack-path", default="", help="external dialect pack path")
    parser.add_argument("--repeat", type=int, default=1, help="number of runs")
    parser.add_argument("--timeout-ms", type=int, default=500, help="timeout per run")
    parser.add_argument("--unknown-only", action="store_true", help="write only unknown parse cases")
    parser.add_argument("--limit", type=int, default=0, help="max records written (0 means unlimited)")
    parser.add_argument("--redact", action="store_true", help="redact raw stdout/stderr and keep summary only")
    parser.add_argument(
        "--output",
        default="./logs/taso-swindle/dfpn-samples.jsonl",
        help="output JSONL path",
    )
    args = parser.parse_args()

    argv = shlex.split(args.dfpn)
    if not argv:
        raise SystemExit("dfpn command is empty")

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    timeout_sec = max(0.05, args.timeout_ms / 1000.0)
    repeat = max(1, int(args.repeat))
    write_limit = max(0, int(args.limit))

    adapter = DfPnAdapter(
        args.dfpn,
        parser_mode=args.parser_mode,
        dialect=args.dialect_requested,
        dialect_pack_path=args.dialect_pack_path,
    )
    packs = getattr(adapter, "_dialect_packs", {})
    pack_source = str(getattr(adapter, "_pack_source", "builtin") or "builtin")

    written = 0
    with out_path.open("a", encoding="utf-8") as fh:
        for idx in range(repeat):
            if write_limit > 0 and written >= write_limit:
                break
            cmd = [*argv, "--position", args.position, "--move", args.move]
            status = "ok"
            returncode = None
            raw_out = ""
            raw_err = ""
            started = time.time()
            try:
                completed = subprocess.run(  # noqa: S603,S607
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                )
                returncode = completed.returncode
                raw_out = completed.stdout or ""
                raw_err = completed.stderr or ""
            except subprocess.TimeoutExpired as exc:
                status = "timeout"
                raw_out = exc.stdout or ""
                raw_err = exc.stderr or ""
            except Exception as exc:  # pragma: no cover - defensive
                status = "error"
                raw_err = str(exc)

            elapsed_ms = int(max(0.0, (time.time() - started) * 1000.0))
            combined = ((raw_out or "") + "\n" + (raw_err or "")).strip()
            parsed = _parse_output(
                combined,
                mode=str(args.parser_mode).upper(),
                dialect=str(args.dialect_requested).upper(),
                packs=packs,
                pack_source=pack_source,
            )

            parse_status = parsed.parser_status
            if status == "timeout":
                parse_status = "timeout"
            elif status == "error":
                parse_status = "error"
            source_detail = parsed.source_detail or f"dfpn:{str(args.dialect_requested).lower()}:unknown_format"
            unknown_reason = None
            if parsed.status == "unknown" or parse_status in {"unknown", "error", "timeout"}:
                unknown_notes = [n for n in parsed.notes if isinstance(n, str) and (n.startswith("dfpn_") or "unknown" in n)]
                if status == "timeout":
                    unknown_reason = "timeout"
                elif status == "error":
                    unknown_reason = "subprocess_error"
                elif unknown_notes:
                    unknown_reason = ",".join(unknown_notes[:3])
                else:
                    unknown_reason = "parse_unknown"

            if args.unknown_only and not (parsed.status == "unknown" or parse_status in {"unknown", "error", "timeout"}):
                continue

            summary_source = combined
            if args.redact:
                summary_source = _redact(combined)
            raw_summary = _short(summary_source)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "sample_index": idx,
                "command": cmd,
                "status": status,
                "returncode": returncode,
                "elapsed_ms": elapsed_ms,
                "raw_out": "" if args.redact else raw_out,
                "raw_err": "" if args.redact else raw_err,
                "raw_summary": raw_summary,
                "dialect_requested": str(args.dialect_requested).upper(),
                "dialect_detected": parsed.dialect_used,
                "parse_status": parse_status,
                "source_detail": source_detail,
                "distance": parsed.distance,
                "unknown_reason": unknown_reason,
                "dfpn_pack_source": pack_source,
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            written += 1

    print(f"wrote {out_path} samples={written}/{repeat}")
    return 0


def _redact(text: str) -> str:
    t = text or ""
    t = re.sub(r"\b[0-9]{2,}\b", "<num>", t)
    t = re.sub(r"[A-Za-z0-9_./-]{16,}", "<token>", t)
    return t


if __name__ == "__main__":
    raise SystemExit(main())
