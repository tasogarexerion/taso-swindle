#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_zip_path(parts_dir: Path) -> Path:
    parts = sorted(parts_dir.glob("*.part.*"))
    if not parts:
        raise FileNotFoundError(f"no part files found in: {parts_dir}")
    first = parts[0].name
    marker = ".part."
    idx = first.find(marker)
    if idx < 0:
        raise ValueError(f"invalid part file name: {first}")
    zip_name = first[:idx]
    return parts_dir.parent / "zip" / zip_name


def _join_parts(parts_dir: Path, output_zip: Path) -> tuple[int, int]:
    parts = sorted(parts_dir.glob("*.part.*"))
    if not parts:
        raise FileNotFoundError(f"no part files found in: {parts_dir}")
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    bytes_total = 0
    with output_zip.open("wb") as out:
        for p in parts:
            data = p.read_bytes()
            out.write(data)
            bytes_total += len(data)
    return len(parts), bytes_total


def _zip_check(path: Path) -> tuple[bool, str]:
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad = zf.testzip()
            if bad:
                return False, f"zip_corrupt:{bad}"
    except Exception as exc:
        return False, f"zip_error:{exc}"
    return True, "ok"


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore split Discord release zip parts and optionally extract.")
    parser.add_argument("--parts-dir", required=True, help="directory containing *.part.* files")
    parser.add_argument("--output-zip", default="", help="output zip path (default derived from parts name)")
    parser.add_argument("--manifest", default="", help="optional manifest.json path")
    parser.add_argument("--extract-dir", default="", help="optional extraction destination")
    parser.add_argument("--extract", action=argparse.BooleanOptionalAction, default=True, help="extract zip after verify")
    parser.add_argument("--summary-out", default="", help="optional summary JSON output")
    args = parser.parse_args()

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts_dir = Path(args.parts_dir).expanduser().resolve()
    if not parts_dir.exists() or not parts_dir.is_dir():
        raise SystemExit(f"parts-dir not found: {parts_dir}")

    output_zip = Path(args.output_zip).expanduser().resolve() if str(args.output_zip).strip() else _default_zip_path(parts_dir)

    manifest_path = None
    if str(args.manifest).strip():
        manifest_path = Path(args.manifest).expanduser().resolve()
    else:
        maybe = parts_dir.parent / "manifest.json"
        if maybe.exists():
            manifest_path = maybe

    manifest = _load_manifest(manifest_path) if manifest_path else {}

    status = "ok"
    errors: list[str] = []
    extracted = ""

    try:
        part_count, joined_bytes = _join_parts(parts_dir, output_zip)
    except Exception as exc:
        status = "failed"
        part_count = 0
        joined_bytes = 0
        errors.append(f"join_failed:{exc}")

    zip_sha = _sha256(output_zip) if output_zip.exists() else ""
    expected_sha = str(manifest.get("zip_sha256", "")) if manifest else ""
    sha_match = (not expected_sha) or (zip_sha == expected_sha)
    if output_zip.exists() and not sha_match:
        status = "failed"
        errors.append("sha256_mismatch")

    zip_ok = False
    zip_check_note = "not_checked"
    if output_zip.exists():
        zip_ok, zip_check_note = _zip_check(output_zip)
        if not zip_ok:
            status = "failed"
            errors.append(zip_check_note)

    extract_dir = Path(args.extract_dir).expanduser().resolve() if str(args.extract_dir).strip() else (parts_dir.parent / "restored")
    if bool(args.extract) and output_zip.exists() and status == "ok":
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(output_zip, "r") as zf:
                zf.extractall(extract_dir)
            launch = extract_dir / "launch_taso_swindle_discord.command"
            if launch.exists():
                launch.chmod(0o755)
            extracted = str(extract_dir)
        except Exception as exc:
            status = "failed"
            errors.append(f"extract_failed:{exc}")

    finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary = {
        "status": status,
        "started_at": started,
        "finished_at": finished,
        "parts_dir": str(parts_dir),
        "part_count": part_count,
        "joined_bytes": joined_bytes,
        "output_zip": str(output_zip),
        "zip_sha256": zip_sha,
        "manifest_path": str(manifest_path) if manifest_path else "",
        "manifest_zip_sha256": expected_sha,
        "sha256_match": bool(sha_match),
        "zip_check_ok": bool(zip_ok),
        "zip_check_note": zip_check_note,
        "extract_enabled": bool(args.extract),
        "extract_dir": extracted,
        "errors": errors,
    }

    if str(args.summary_out).strip():
        out = Path(args.summary_out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
