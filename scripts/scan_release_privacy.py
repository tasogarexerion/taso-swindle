#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DENY_FILE_PATTERNS: tuple[str, ...] = (
    "*.jsonl",
    "*.kif",
    "*.csa",
    "*learning*",
    "*history*",
    "*report*",
)

DEFAULT_DENY_TEXT_PATTERNS: tuple[str, ...] = (
    "/Users/",
    "external_kifu",
    "shogi-extend",
    "source_log_path",
    "K_Yamawasabi",
)

TEXT_EXTENSIONS: tuple[str, ...] = (
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".command",
    ".sh",
    ".ini",
    ".cfg",
    ".toml",
)


@dataclass(frozen=True)
class ScanResult:
    ok: bool
    status: str
    target_dir: str
    scanned_files: int
    scanned_text_files: int
    denied_file_hits: list[dict[str, Any]]
    text_pattern_hits: list[dict[str, Any]]
    deny_file_patterns: list[str]
    deny_text_patterns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "target_dir": self.target_dir,
            "scanned_files": self.scanned_files,
            "scanned_text_files": self.scanned_text_files,
            "denied_file_hits": self.denied_file_hits,
            "text_pattern_hits": self.text_pattern_hits,
            "deny_file_patterns": self.deny_file_patterns,
            "deny_text_patterns": self.deny_text_patterns,
        }


def _is_text_candidate(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    name = path.name.lower()
    return name in {"readme", "license", "manifest", "summary"}


def _is_binary_file(path: Path) -> bool:
    try:
        data = path.read_bytes()[:4096]
    except Exception:
        return True
    return b"\x00" in data


def _find_text_hits(path: Path, pattern: str) -> list[int]:
    hits: list[int] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            for idx, raw in enumerate(fh, start=1):
                if pattern in raw:
                    hits.append(idx)
    except Exception:
        return []
    return hits


def run_scan(
    *,
    target_dir: Path,
    deny_file_patterns: tuple[str, ...] = DEFAULT_DENY_FILE_PATTERNS,
    deny_text_patterns: tuple[str, ...] = DEFAULT_DENY_TEXT_PATTERNS,
) -> ScanResult:
    target_dir = target_dir.resolve()
    denied_file_hits: list[dict[str, Any]] = []
    text_pattern_hits: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_text_files = 0

    files = sorted([p for p in target_dir.rglob("*") if p.is_file()])
    for path in files:
        scanned_files += 1
        rel = str(path.relative_to(target_dir))
        rel_lower = rel.lower()

        for patt in deny_file_patterns:
            if fnmatch.fnmatch(rel_lower, patt.lower()) or fnmatch.fnmatch(path.name.lower(), patt.lower()):
                denied_file_hits.append({"file": rel, "pattern": patt})

        if not _is_text_candidate(path):
            continue
        if _is_binary_file(path):
            continue
        scanned_text_files += 1
        for patt in deny_text_patterns:
            lines = _find_text_hits(path, patt)
            if lines:
                text_pattern_hits.append(
                    {
                        "file": rel,
                        "pattern": patt,
                        "line_hits": lines[:20],
                        "hit_count": len(lines),
                    }
                )

    ok = (len(denied_file_hits) == 0 and len(text_pattern_hits) == 0)
    return ScanResult(
        ok=ok,
        status="ok" if ok else "failed",
        target_dir=str(target_dir),
        scanned_files=scanned_files,
        scanned_text_files=scanned_text_files,
        denied_file_hits=denied_file_hits,
        text_pattern_hits=text_pattern_hits,
        deny_file_patterns=list(deny_file_patterns),
        deny_text_patterns=list(deny_text_patterns),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan release bundle for privacy/compliance deny rules.")
    parser.add_argument("--target-dir", required=True, help="release bundle directory")
    parser.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True, help="fail on any hit")
    parser.add_argument("--json-out", default="", help="optional JSON output path")
    args = parser.parse_args()

    target_dir = Path(args.target_dir).expanduser()
    if not target_dir.exists() or not target_dir.is_dir():
        raise SystemExit(f"target-dir not found: {target_dir}")

    result = run_scan(target_dir=target_dir)
    payload = result.to_dict()
    if str(args.json_out).strip():
        out = Path(args.json_out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))

    if bool(args.strict) and not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

