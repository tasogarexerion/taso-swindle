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
KANJI_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _ts_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _http_json_get(url: str, timeout_sec: float = 30.0) -> dict[str, Any]:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "taso-swindle/highdan-collector"})
    with urlopen(req, timeout=timeout_sec) as res:  # noqa: S310
        raw = res.read().decode("utf-8", errors="replace")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _fetch_records_page(query: str, page: int, per: int, timeout_sec: float) -> dict[str, Any]:
    params = {"query": query, "page": page, "per": per, "sort_column": "battled_at", "sort_order": "desc"}
    url = f"{BASE_URL}/w.json?{urlencode(params)}"
    return _http_json_get(url, timeout_sec=timeout_sec)


def _parse_dan(raw: str) -> int:
    s = str(raw or "").strip()
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    if digits and "段" in s:
        try:
            return int(digits)
        except Exception:
            return 0
    for k, v in KANJI_NUM.items():
        if f"{k}段" in s:
            return v
    return 0


def _record_has_user_key(rec: dict[str, Any], user_key: str) -> bool:
    memberships = rec.get("memberships")
    if not isinstance(memberships, list):
        return False
    target = (user_key or "").strip()
    if not target:
        return False
    for row in memberships:
        if not isinstance(row, dict):
            continue
        user = row.get("user")
        if not isinstance(user, dict):
            continue
        if str(user.get("key", "")).strip() == target:
            return True
    return False


def _extract_highdan_users(rec: dict[str, Any], min_dan: int, max_dan: int) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    memberships = rec.get("memberships")
    if not isinstance(memberships, list):
        return out
    for row in memberships:
        if not isinstance(row, dict):
            continue
        user = row.get("user")
        if not isinstance(user, dict):
            continue
        key = str(user.get("key", "")).strip()
        if not key:
            continue
        grade = row.get("grade_info")
        grade_name = ""
        if isinstance(grade, dict):
            grade_name = str(grade.get("name", "")).strip()
        dan = _parse_dan(grade_name)
        if dan < min_dan:
            continue
        if max_dan > 0 and dan > max_dan:
            continue
        out.append((key, dan))
    return out


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_existing_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists():
        return keys
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
            key = str(rec.get("key", "")).strip()
            if key:
                keys.add(key)
    return keys


def _fetch_user_records(
    *,
    user_key: str,
    per: int,
    max_games_per_user: int,
    timeout_sec: float,
    strict_filter: bool,
) -> tuple[list[dict[str, Any]], int]:
    page = 1
    records: list[dict[str, Any]] = []
    total_hint = 0
    while True:
        data = _fetch_records_page(query=user_key, page=page, per=per, timeout_sec=timeout_sec)
        if page == 1:
            try:
                total_hint = int(data.get("total", 0) or 0)
            except Exception:
                total_hint = 0
        chunk = data.get("records")
        if not isinstance(chunk, list) or not chunk:
            break
        for rec in chunk:
            if not isinstance(rec, dict):
                continue
            if strict_filter and (not _record_has_user_key(rec, user_key)):
                continue
            records.append(rec)
            if max_games_per_user > 0 and len(records) >= max_games_per_user:
                break
        if max_games_per_user > 0 and len(records) >= max_games_per_user:
            break
        if total_hint > 0 and len(records) >= total_hint:
            break
        page += 1
    return records, total_hint


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect 6-dan+ corpus from shogi-extend by recursive user discovery.")
    parser.add_argument("--seed-user", default="K_Yamawasabi", help="starting user key")
    parser.add_argument("--min-dan", type=int, default=6, help="minimum dan to keep/discover users")
    parser.add_argument("--max-dan", type=int, default=0, help="maximum dan to keep/discover users (0=unlimited)")
    parser.add_argument("--exclude-user", action="append", default=[], help="user key(s) to exclude from queue")
    parser.add_argument("--output-root", default="artifacts/external_kifu/highdan_corpus", help="output root")
    parser.add_argument("--run-dir", default="", help="existing run directory for resume")
    parser.add_argument("--resume", action="store_true", help="resume from run-dir/state")
    parser.add_argument("--max-users", type=int, default=0, help="0=unlimited")
    parser.add_argument("--per", type=int, default=50, help="records per page")
    parser.add_argument("--max-games-per-user", type=int, default=0, help="0=all")
    parser.add_argument("--timeout-sec", type=float, default=30.0)
    parser.add_argument("--sleep-sec", type=float, default=0.05)
    parser.add_argument("--strict-filter-user-key", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    enforce_external_kifu_fetch_policy(script_name="collect_shogi_extend_highdan_corpus.py")

    root = Path(__file__).resolve().parent.parent
    out_root = Path(args.output_root).expanduser()
    if not out_root.is_absolute():
        out_root = (root / out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if args.run_dir.strip():
        run_dir = Path(args.run_dir).expanduser()
        if not run_dir.is_absolute():
            run_dir = (root / run_dir).resolve()
    else:
        run_dir = out_root / f"highdan-{_ts_id()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    state_path = run_dir / "state.json"
    records_path = run_dir / "records_dedup.jsonl"
    users_dir = run_dir / "users"
    users_dir.mkdir(parents=True, exist_ok=True)

    state = _load_state(state_path) if bool(args.resume) else {}
    queue: list[str] = [str(x) for x in state.get("queue", []) if str(x).strip()] if state else []
    processed: set[str] = set(str(x) for x in state.get("processed", []) if str(x).strip()) if state else set()
    discovered: dict[str, int] = {}
    if state:
        raw_discovered = state.get("discovered", {})
        if isinstance(raw_discovered, dict):
            for k, v in raw_discovered.items():
                try:
                    discovered[str(k)] = int(v)
                except Exception:
                    continue

    excludes = {str(x).strip() for x in list(args.exclude_user) if str(x).strip()}
    excludes.add(str(args.seed_user).strip())
    def refill_queue_if_needed() -> None:
        if queue:
            return
        if discovered:
            refill = sorted(
                [(u, int(d)) for u, d in discovered.items() if str(u).strip() and str(u).strip() not in processed and str(u).strip() not in excludes],
                key=lambda kv: (-kv[1], kv[0]),
            )
            queue.extend([u for u, _d in refill])
        elif str(args.seed_user).strip():
            queue.append(str(args.seed_user).strip())

    refill_queue_if_needed()

    record_keys = _load_existing_keys(records_path)

    fetch_errors: list[str] = list(state.get("fetch_errors", [])) if state else []
    user_stats: list[dict[str, Any]] = list(state.get("user_stats", [])) if state else []

    started = time.time()

    while True:
        if not queue:
            refill_queue_if_needed()
            if not queue:
                break
        if args.max_users > 0 and len(processed) >= int(args.max_users):
            break

        user = queue.pop(0).strip()
        if not user or user in processed:
            continue

        try:
            records, total_hint = _fetch_user_records(
                user_key=user,
                per=max(1, min(200, int(args.per))),
                max_games_per_user=max(0, int(args.max_games_per_user)),
                timeout_sec=max(3.0, float(args.timeout_sec)),
                strict_filter=bool(args.strict_filter_user_key),
            )
        except Exception as exc:  # pragma: no cover
            fetch_errors.append(f"{user}: {exc}")
            processed.add(user)
            continue

        raw_count = len(records)
        new_count = 0
        with records_path.open("a", encoding="utf-8") as out:
            for rec in records:
                key = str(rec.get("key", "")).strip()
                if not key:
                    continue
                if key in record_keys:
                    continue
                record_keys.add(key)
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                new_count += 1

                for key2, dan2 in _extract_highdan_users(
                    rec,
                    min_dan=max(1, int(args.min_dan)),
                    max_dan=max(0, int(args.max_dan)),
                ):
                    prev = discovered.get(key2, 0)
                    if dan2 > prev:
                        discovered[key2] = dan2
                    if key2 in processed or key2 in queue:
                        continue
                    if key2 in excludes:
                        continue
                    if args.max_users > 0 and (len(processed) + len(queue) + 1) > int(args.max_users):
                        continue
                    queue.append(key2)

        user_record_path = users_dir / f"{user}.records.jsonl"
        with user_record_path.open("w", encoding="utf-8") as fh:
            for rec in records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

        processed.add(user)
        user_stats.append(
            {
                "user": user,
                "records_raw": raw_count,
                "records_new": new_count,
                "total_hint": total_hint,
                "queue_size_after": len(queue),
                "processed_count": len(processed),
                "record_keys_total": len(record_keys),
            }
        )
        print(
            f"[collect] user={user} raw={raw_count} new={new_count} "
            f"processed={len(processed)} queue={len(queue)} uniq_games={len(record_keys)}"
        )

        state_payload = {
            "run_dir": str(run_dir),
            "seed_user": args.seed_user,
            "min_dan": int(args.min_dan),
            "max_dan": int(args.max_dan),
            "queue": queue,
            "processed": sorted(processed),
            "discovered": discovered,
            "fetch_errors": fetch_errors,
            "user_stats": user_stats,
            "record_keys_total": len(record_keys),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write_json(state_path, state_payload)

        if float(args.sleep_sec) > 0:
            time.sleep(float(args.sleep_sec))

    finished = time.time()
    highdan_users = sorted(
        [
            (k, v)
            for k, v in discovered.items()
            if int(v) >= int(args.min_dan) and (int(args.max_dan) <= 0 or int(v) <= int(args.max_dan))
        ],
        key=lambda kv: (-kv[1], kv[0]),
    )
    summary = {
        "run_dir": str(run_dir),
        "seed_user": args.seed_user,
        "min_dan": int(args.min_dan),
        "max_dan": int(args.max_dan),
        "processed_users": len(processed),
        "queued_users": len(queue),
        "unique_games": len(record_keys),
        "highdan_discovered": len(highdan_users),
        "fetch_errors": fetch_errors,
        "records_path": str(records_path),
        "user_stats_path": str(run_dir / "user_stats.json"),
        "started_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(timespec="seconds"),
        "finished_at": datetime.fromtimestamp(finished, tz=timezone.utc).isoformat(timespec="seconds"),
        "duration_sec": round(finished - started, 3),
        "max_users": int(args.max_users),
        "max_games_per_user": int(args.max_games_per_user),
    }
    _write_json(run_dir / "summary.json", summary)
    _write_json(run_dir / "user_stats.json", {"user_stats": user_stats})
    _write_json(
        run_dir / "highdan_users.json",
        {"min_dan": int(args.min_dan), "max_dan": int(args.max_dan), "users": [{"user": u, "dan": d} for u, d in highdan_users]},
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
