#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from dfpn_token_classifier import predict as predict_token_class
from export_dfpn_candidate_features import extract_candidate_features, write_feature_rows


TOKEN_RE = re.compile(r"[A-Za-z_]{2,}|[\u3040-\u30ff\u3400-\u9fff]{2,}")
DISTANCE_PATTERNS = [
    ("distance:ply", re.compile(r"\b(\d+)\s*ply\b", re.IGNORECASE), r"\b(\d+)\s*ply\b"),
    ("distance:in_n", re.compile(r"\bin\s+(\d+)\b", re.IGNORECASE), r"\bin\s+(\d+)\b"),
    ("distance:te", re.compile(r"(\d+)\s*手"), r"(\d+)\s*手"),
]
NEGATION_HINTS_EN = ("no", "not", "none", "without", "unfound", "unable", "fail", "false")
NEGATION_HINTS_JA = ("なし", "不詰", "詰まず", "詰まない", "見つから", "不成立", "未検出")
POSITIVE_HINTS_EN = ("mate", "mated", "win", "found")
POSITIVE_HINTS_JA = ("詰み", "必至", "勝ち", "発見")


@dataclass(frozen=True)
class ProposalKey:
    token: str
    token_class: str
    language: str
    regex: str


@dataclass
class ProposalAccum:
    key: ProposalKey
    count: int
    examples: list[str]


def _iter_unknown_records(path: Path) -> Iterable[dict]:
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
                yield rec


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


def _short(text: str, limit: int = 140) -> str:
    normalized = " ".join((text or "").splitlines())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit]


def _detect_lang(token: str) -> str:
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", token):
        return "ja"
    if re.search(r"[A-Za-z]", token):
        return "en"
    return "mixed"


def _classify_token(token: str) -> str:
    low = token.lower()
    if any(k in low for k in NEGATION_HINTS_EN):
        return "mate_negative"
    if any(k in token for k in NEGATION_HINTS_JA):
        return "mate_negative"
    if any(k in low for k in POSITIVE_HINTS_EN):
        return "mate_positive"
    if any(k in token for k in POSITIVE_HINTS_JA):
        return "mate_positive"
    return "unknown_marker"


def _confidence_hint(count: int, token_class: str) -> float:
    base = 0.35 + min(0.45, 0.08 * float(max(1, count)))
    if token_class in {"mate_negative", "distance"}:
        base += 0.12
    elif token_class == "mate_positive":
        base += 0.06
    return max(0.0, min(1.0, base))


def _language_allowed(token_lang: str, target: str) -> bool:
    t = (target or "auto").lower()
    if t in {"auto", "mixed"}:
        return True
    return token_lang == t


def _token_priority(token_class: str) -> int:
    if token_class == "mate_negative":
        return 0
    if token_class == "distance":
        return 1
    if token_class == "mate_positive":
        return 2
    return 3


def _build_suggestion(token_class: str, regex: str, token: str) -> tuple[str, object]:
    if token_class == "distance":
        return "distance_patterns", regex
    if token_class == "mate_negative":
        return "negation_patterns", regex
    if token_class == "mate_positive":
        return "loose_patterns", [regex, "for_us", "mate_hint"]
    return "loose_patterns", [regex, "unknown", "unknown_marker"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build df-pn dialect pack candidate regex fragments from collected unknown samples.")
    parser.add_argument("--input", required=True, help="input samples JSONL from collect_dfpn_samples.py")
    parser.add_argument("--output", default="", help="output candidate JSON path (default: dfpn_dialects/candidates_*.json)")
    parser.add_argument("--min-support", type=int, default=2, help="minimum token support count")
    parser.add_argument("--min-count", type=int, default=0, help="deprecated alias of --min-support")
    parser.add_argument("--max-proposals", type=int, default=40, help="maximum candidate proposals")
    parser.add_argument("--language", choices=["auto", "ja", "en", "mixed"], default="auto", help="token language filter")
    parser.add_argument("--with-negation", action=argparse.BooleanOptionalAction, default=True, help="include negation candidate tokens")
    parser.add_argument("--with-distance", action=argparse.BooleanOptionalAction, default=True, help="include distance regex candidates")
    parser.add_argument("--features-out", default="", help="optional output path for candidate features (jsonl/csv)")
    parser.add_argument("--features-format", choices=["jsonl", "csv"], default="jsonl", help="feature output format")
    parser.add_argument("--classifier-model", default="", help="optional supervised classifier model JSON")
    parser.add_argument("--classifier-min-confidence", type=float, default=0.65, help="minimum confidence to adopt model token_class")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"input not found: {in_path}")

    if args.output:
        out_path = Path(args.output)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = Path("dfpn_dialects") / f"candidates_{stamp}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    min_support = max(1, int(args.min_support))
    if int(args.min_count) > 0:
        min_support = max(min_support, int(args.min_count))
    max_props = max(1, int(args.max_proposals))

    counts: Counter[ProposalKey] = Counter()
    examples: dict[ProposalKey, list[str]] = defaultdict(list)
    language_counts: Counter[str] = Counter()
    unknown_count = 0
    unknown_records = list(_iter_unknown_records(in_path))
    classifier_model: dict | None = None
    raw_model = (args.classifier_model or "").strip()
    if raw_model:
        model_path = Path(raw_model)
        if model_path.exists():
            try:
                payload = json.loads(model_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    classifier_model = payload
            except Exception:
                classifier_model = None

    for rec in unknown_records:
        unknown_count += 1
        summary = str(rec.get("raw_summary", "") or "")
        summary_short = _short(summary)

        for token in _extract_tokens(summary):
            lang = _detect_lang(token)
            language_counts[lang] += 1
            if not _language_allowed(lang, args.language):
                continue

            token_class = _classify_token(token)
            if token_class == "mate_negative" and not args.with_negation:
                continue
            regex = re.escape(token)
            key = ProposalKey(token=token, token_class=token_class, language=lang, regex=regex)
            counts[key] += 1
            if summary_short and len(examples[key]) < 3 and summary_short not in examples[key]:
                examples[key].append(summary_short)

        if args.with_distance:
            for _, cre, regex in DISTANCE_PATTERNS:
                m = cre.search(summary)
                if not m:
                    continue
                key = ProposalKey(token=regex, token_class="distance", language="mixed", regex=regex)
                counts[key] += 1
                if summary_short and len(examples[key]) < 3 and summary_short not in examples[key]:
                    examples[key].append(summary_short)

    ranked_keys = sorted(
        [k for k, v in counts.items() if v >= min_support],
        key=lambda k: (_token_priority(k.token_class), -counts[k], k.token.lower()),
    )

    feature_rows_all = extract_candidate_features(
        unknown_records,
        language="auto",
        min_support=1,
    )
    feature_row_by_token = {
        str(row.get("token", "")): row
        for row in feature_rows_all
        if isinstance(row, dict) and str(row.get("token", ""))
    }

    proposals: list[dict] = []
    classifier_min_conf = max(0.0, min(1.0, float(args.classifier_min_confidence)))
    for key in ranked_keys:
        if len(proposals) >= max_props:
            break
        try:
            re.compile(key.regex)
        except re.error:
            continue

        final_class = key.token_class
        class_source = "heuristic"
        class_conf = _confidence_hint(int(counts[key]), key.token_class)
        class_pred = key.token_class
        if classifier_model is not None and key.token_class != "distance":
            row = feature_row_by_token.get(key.token)
            if isinstance(row, dict):
                pred, conf, _ = predict_token_class(classifier_model, row)
                class_pred = pred
                if conf >= classifier_min_conf:
                    final_class = pred
                    class_source = "model"
                    class_conf = conf
                else:
                    class_source = "heuristic_model_low_conf"

        suggested_section, suggested_entry = _build_suggestion(final_class, key.regex, key.token)
        sample_count = int(counts[key])
        proposal = {
            "token": key.token,
            "count": sample_count,
            "sample_count": sample_count,
            "language": key.language,
            "token_class": final_class,
            "token_class_heuristic": key.token_class,
            "token_class_predicted": class_pred,
            "token_class_source": class_source,
            "token_class_confidence": max(0.0, min(1.0, float(class_conf))),
            "regex": key.regex,
            "regex_compile_ok": True,
            "suggested_section": suggested_section,
            "suggested_entry": suggested_entry,
            "examples": list(examples.get(key, [])),
            "example": (examples.get(key, [""])[0] if examples.get(key) else ""),
            "confidence_hint": _confidence_hint(sample_count, final_class),
        }
        proposals.append(proposal)

    payload = {
        "version": 2,
        "kind": "dfpn_pack_candidates",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_input": str(in_path),
        "unknown_records": unknown_count,
        "language": args.language,
        "with_negation": bool(args.with_negation),
        "with_distance": bool(args.with_distance),
        "min_support": min_support,
        "max_proposals": max_props,
        "language_counts": dict(language_counts),
        "proposals": proposals,
        "note": "proposal-only; do not auto-apply to default_packs.json without review",
    }
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    if (args.features_out or "").strip():
        feature_rows = extract_candidate_features(unknown_records, language=args.language, min_support=min_support)
        features_out_path = Path(args.features_out)
        write_feature_rows(feature_rows, features_out_path, fmt=args.features_format)
        print(f"wrote {features_out_path} rows={len(feature_rows)} format={args.features_format}")

    print(f"wrote {out_path} proposals={len(proposals)} unknown_records={unknown_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
