#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from project_policies import enforce_external_kifu_fetch_policy


BASE_URL = "https://www.shogi-extend.com"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _http_json_get(url: str, timeout_sec: float = 30.0) -> dict[str, Any]:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "taso-swindle/phase-extract"})
    with urlopen(req, timeout=timeout_sec) as res:  # noqa: S310
        raw = res.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _http_json_post(url: str, payload: dict[str, Any], timeout_sec: float = 30.0) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "taso-swindle/phase-extract",
        },
        method="POST",
    )
    with urlopen(req, timeout=timeout_sec) as res:  # noqa: S310
        raw = res.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _fetch_records_page(user_key: str, page: int, per: int) -> dict[str, Any]:
    params = {"query": user_key, "page": page, "per": per, "sort_column": "battled_at", "sort_order": "desc"}
    url = f"{BASE_URL}/w.json?{urlencode(params)}"
    return _http_json_get(url)


def _convert_battle_to_format(battle_key: str, to_format: str) -> tuple[str, int]:
    source = f"{BASE_URL}/w/{battle_key}.json"
    payload = {"any_source": source, "to_format": to_format}
    data = _http_json_post(f"{BASE_URL}/api/general/any_source_to.json", payload)
    body = str(data.get("body", ""))
    turn_max = int(data.get("turn_max", 0) or 0)
    return body, turn_max


def _safe_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _record_has_user_key(rec: dict[str, Any], user_key: str) -> bool:
    target = (user_key or "").strip()
    if not target:
        return False
    memberships = rec.get("memberships")
    if not isinstance(memberships, list):
        return False
    for row in memberships:
        if not isinstance(row, dict):
            continue
        user = row.get("user")
        if not isinstance(user, dict):
            continue
        key = str(user.get("key", "")).strip()
        if key == target:
            return True
    return False


def _collect_queries(user_key: str, base_query: str, extra_queries: list[str]) -> list[str]:
    ordered: list[str] = []
    for raw in [base_query, user_key, *extra_queries]:
        q = str(raw or "").strip()
        if not q:
            continue
        if q in ordered:
            continue
        ordered.append(q)
    return ordered


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract SHOGI-EXTEND SWARS records/KIF/CSA for a user key.")
    parser.add_argument("--user-key", required=True, help="SWARS user key, e.g. K_Yamawasabi")
    parser.add_argument(
        "--query",
        default="",
        help="primary query for w.json (default: user-key). combine with --extra-query for union-fetch",
    )
    parser.add_argument(
        "--extra-query",
        action="append",
        default=[],
        help="additional query term(s); can be passed multiple times",
    )
    parser.add_argument(
        "--filter-user-key",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep only records where memberships include --user-key",
    )
    parser.add_argument("--output-root", default="artifacts/external_kifu", help="output root directory")
    parser.add_argument("--per", type=int, default=50, help="records per page (1..200)")
    parser.add_argument("--max-games", type=int, default=0, help="0 means all")
    parser.add_argument("--fetch-kif", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fetch-csa", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--sleep-sec", type=float, default=0.03, help="sleep between conversion requests")
    parser.add_argument("--timeout-sec", type=float, default=30.0, help="HTTP timeout seconds")
    args = parser.parse_args()

    enforce_external_kifu_fetch_policy(script_name="extract_shogi_extend_user_kifu.py")

    user_key = args.user_key.strip()
    if not user_key:
        raise SystemExit("user key is empty")
    per = max(1, min(200, int(args.per)))
    max_games = max(0, int(args.max_games))
    query_list = _collect_queries(user_key=user_key, base_query=(args.query or user_key), extra_queries=list(args.extra_query))
    if not query_list:
        raise SystemExit("query list is empty")

    root = Path(__file__).resolve().parent.parent
    out_root = Path(args.output_root).expanduser()
    if not out_root.is_absolute():
        out_root = (root / out_root).resolve()
    run_dir = out_root / f"{user_key}-{_ts_id()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rec_jsonl = run_dir / "records.jsonl"
    keys_txt = run_dir / "battle_keys.txt"
    kif_dir = run_dir / "kif"
    csa_dir = run_dir / "csa"

    total = 0
    records: list[dict[str, Any]] = []
    fetch_errors: list[str] = []
    seen_keys: set[str] = set()
    pages_fetched_total = 0
    query_stats: list[dict[str, Any]] = []
    for query in query_list:
        page = 1
        query_total = 0
        query_raw = 0
        query_kept = 0
        while True:
            try:
                data = _fetch_records_page(user_key=query, page=page, per=per)
            except Exception as exc:  # pragma: no cover
                fetch_errors.append(f"query={query}:page={page}: {exc}")
                break

            pages_fetched_total += 1
            if page == 1:
                try:
                    query_total = int(data.get("total", 0) or 0)
                except Exception:
                    query_total = 0
                total += query_total

            chunk = data.get("records")
            if not isinstance(chunk, list) or not chunk:
                break
            for rec in chunk:
                if not isinstance(rec, dict):
                    continue
                query_raw += 1
                key = str(rec.get("key", "")).strip()
                if not key:
                    continue
                if bool(args.filter_user_key) and not _record_has_user_key(rec, user_key):
                    continue
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                records.append(rec)
                query_kept += 1
                if max_games > 0 and len(records) >= max_games:
                    break
            if max_games > 0 and len(records) >= max_games:
                break
            if query_total > 0 and query_raw >= query_total:
                break
            page += 1

        query_stats.append(
            {
                "query": query,
                "query_total_hint": query_total,
                "records_raw": query_raw,
                "records_kept_new": query_kept,
                "pages_last": page,
            }
        )
        if max_games > 0 and len(records) >= max_games:
            break

    with rec_jsonl.open("w", encoding="utf-8") as out:
        for rec in records:
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")

    keys: list[str] = []
    for rec in records:
        key = str(rec.get("key", "")).strip()
        if key:
            keys.append(key)
    keys_txt.write_text("\n".join(keys) + ("\n" if keys else ""), encoding="utf-8")

    converted_kif = 0
    converted_csa = 0
    convert_errors: list[str] = []
    for idx, key in enumerate(keys, start=1):
        if bool(args.fetch_kif):
            try:
                body, _turn_max = _convert_battle_to_format(key, "kif")
                _safe_write_text(kif_dir / f"{key}.kif", body)
                converted_kif += 1
            except Exception as exc:  # pragma: no cover
                convert_errors.append(f"kif:{key}:{exc}")
        if bool(args.fetch_csa):
            try:
                body, _turn_max = _convert_battle_to_format(key, "csa")
                _safe_write_text(csa_dir / f"{key}.csa", body)
                converted_csa += 1
            except Exception as exc:  # pragma: no cover
                convert_errors.append(f"csa:{key}:{exc}")
        if float(args.sleep_sec) > 0:
            time.sleep(float(args.sleep_sec))
        if idx % 20 == 0 or idx == len(keys):
            print(f"[extract] {idx}/{len(keys)} done")

    summary = {
        "user_key": user_key,
        "started_at": _utc_now(),
        "queries": query_list,
        "filter_user_key": bool(args.filter_user_key),
        "records_count": len(records),
        "total_hint_sum": total,
        "pages_fetched": pages_fetched_total,
        "query_stats": query_stats,
        "fetch_kif": bool(args.fetch_kif),
        "fetch_csa": bool(args.fetch_csa),
        "converted_kif": converted_kif,
        "converted_csa": converted_csa,
        "fetch_errors": fetch_errors,
        "convert_errors": convert_errors,
        "records_jsonl": str(rec_jsonl),
        "keys_txt": str(keys_txt),
        "kif_dir": str(kif_dir),
        "csa_dir": str(csa_dir),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
