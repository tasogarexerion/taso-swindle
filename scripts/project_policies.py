#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from typing import Any


# Project rule: disallow external kifu acquisition from network services.
EXTERNAL_KIFU_FETCH_ALLOWED = False


def enforce_external_kifu_fetch_policy(*, script_name: str) -> None:
    if EXTERNAL_KIFU_FETCH_ALLOWED:
        return
    msg = (
        f"[policy_blocked] {script_name}: 外部からの棋譜取得は禁止されています。"
        "既存のローカル棋譜のみ使用してください。"
    )
    raise SystemExit(msg)


def anonymize_learning_sample(
    record: dict[str, Any],
    *,
    salt: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    if not enabled:
        return record
    out = dict(record)
    for field in ("game_id", "game_id_raw", "game_id_normalized", "source_log_path", "_source_log_path"):
        out[field] = _anonymize_value(out.get(field), field=field, salt=salt)
    backend = out.get("backend_engine_info")
    if isinstance(backend, dict):
        backend = dict(backend)
        backend["author"] = _anonymize_value(backend.get("author"), field="backend_engine_info.author", salt=salt)
        out["backend_engine_info"] = backend
    return out


def _anonymize_value(value: Any, *, field: str, salt: str) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return text
    payload = f"{field}:{salt}:{text}".encode("utf-8", errors="replace")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"anon:{field}:{digest}"
