#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


TOKEN_RE = re.compile(r"[A-Za-z_]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}")
NEGATION_HINTS_EN = ("no", "not", "none", "without", "unfound", "unable", "fail", "false")
NEGATION_HINTS_JA = ("なし", "不詰", "詰まず", "詰まない", "見つから", "不成立", "未検出")


def iter_unknown_records(path: Path) -> list[dict[str, Any]]:
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
            if not isinstance(rec, dict):
                continue
            parse_status = str(rec.get("parse_status", "unknown")).lower()
            unknown_reason = rec.get("unknown_reason")
            if parse_status in {"unknown", "error", "timeout"} or unknown_reason:
                out.append(rec)
    return out


def _extract_tokens(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for m in TOKEN_RE.finditer(text):
        tok = m.group(0).strip()
        if not tok:
            continue
        low = tok.lower()
        if low in {"info", "score", "depth", "nodes", "bestmove", "unknown"}:
            continue
        out.append(tok)
    return out


def _detect_lang(token: str) -> str:
    has_ja = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", token))
    has_en = bool(re.search(r"[A-Za-z]", token))
    if has_ja and not has_en:
        return "ja"
    if has_en and not has_ja:
        return "en"
    if has_ja and has_en:
        return "mixed"
    return "other"


def _classify_token(token: str) -> str:
    low = token.lower()
    if any(k in low for k in NEGATION_HINTS_EN) or any(k in token for k in NEGATION_HINTS_JA):
        return "mate_negative"
    if re.search(r"\d+\s*手", token) or re.search(r"\d+\s*ply", low) or re.search(r"in\s*\d+", low):
        return "distance"
    if "mate" in low or "詰" in token or "勝" in token:
        return "mate_positive"
    return "unknown_marker"


def _language_allowed(token_lang: str, target: str) -> bool:
    t = (target or "auto").lower()
    if t in {"auto", "mixed"}:
        return True
    return token_lang == t


def extract_candidate_features(records: Iterable[dict[str, Any]], *, language: str = "auto", min_support: int = 1) -> list[dict[str, Any]]:
    token_count: Counter[str] = Counter()
    token_unknown_reason: dict[str, Counter[str]] = defaultdict(Counter)
    context_count: Counter[str] = Counter()

    per_sample_tokens: list[tuple[list[str], str]] = []
    for rec in records:
        summary = str(rec.get("raw_summary", "") or "")
        unknown_reason = str(rec.get("unknown_reason", "") or "")
        tokens = _extract_tokens(summary)
        per_sample_tokens.append((tokens, unknown_reason))

    for tokens, unknown_reason in per_sample_tokens:
        uniq = set(tokens)
        for tok in tokens:
            token_count[tok] += 1
            token_unknown_reason[tok][unknown_reason or "unknown"] += 1
            context_count[tok] += max(0, len(uniq) - 1)

    rows: list[dict[str, Any]] = []
    threshold = max(1, int(min_support))
    for token, count in token_count.most_common():
        if count < threshold:
            continue
        lang = _detect_lang(token)
        if not _language_allowed(lang, language):
            continue

        token_len = len(token)
        digit_count = sum(1 for ch in token if ch.isdigit())
        ja_count = sum(1 for ch in token if re.match(r"[\u3040-\u30ff\u3400-\u9fff]", ch))
        en_count = sum(1 for ch in token if re.match(r"[A-Za-z]", ch))
        alpha_num = max(1, token_len)
        neg_hit = bool(any(k in token.lower() for k in NEGATION_HINTS_EN) or any(k in token for k in NEGATION_HINTS_JA))
        dist_hit = bool(re.search(r"\d", token) and ("ply" in token.lower() or "手" in token or "in" in token.lower()))

        rows.append(
            {
                "token": token,
                "sample_count": int(count),
                "token_len": token_len,
                "has_digit": bool(digit_count > 0),
                "digit_count": digit_count,
                "ja_ratio": float(ja_count) / float(alpha_num),
                "en_ratio": float(en_count) / float(alpha_num),
                "language": lang,
                "negation_hit": neg_hit,
                "distance_hit": dist_hit,
                "token_class": _classify_token(token),
                "context_freq": int(context_count[token]),
                "unknown_reason_top": token_unknown_reason[token].most_common(1)[0][0],
            }
        )
    return rows


def write_feature_rows(rows: list[dict[str, Any]], out_path: Path, fmt: str = "jsonl") -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        fields = [
            "token",
            "sample_count",
            "token_len",
            "has_digit",
            "digit_count",
            "ja_ratio",
            "en_ratio",
            "language",
            "negation_hit",
            "distance_hit",
            "token_class",
            "context_freq",
            "unknown_reason_top",
        ]
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return

    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export candidate token features for df-pn unknown samples.")
    parser.add_argument("--input", required=True, help="input df-pn samples JSONL")
    parser.add_argument("--output", required=True, help="output feature file path")
    parser.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    parser.add_argument("--language", choices=["auto", "ja", "en", "mixed"], default="auto")
    parser.add_argument("--min-support", type=int, default=1)
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    rows = extract_candidate_features(
        iter_unknown_records(in_path),
        language=args.language,
        min_support=max(1, int(args.min_support)),
    )
    out_path = Path(args.output)
    write_feature_rows(rows, out_path, fmt=str(args.format))
    print(f"wrote {out_path} rows={len(rows)} format={args.format}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
