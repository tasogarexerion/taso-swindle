#!/usr/bin/env python3
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse
import re


KNOWN_QUERY_KEYS = ("game_id", "gameid", "id", "gid", "kifid", "kifu", "record")
SOURCE_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("wars", ("shogiwars.heroz.jp", "shogiwars", "wars", "wrs", "heroz")),
    ("81dojo", ("81dojo.com", "81dojo", "dojo")),
    ("lishogi", ("lishogi.org", "lishogi", "lila")),
    ("shogiclub24", ("81square", "shogiclub24", "club24", "shogiclub-24")),
    ("shogiquest", ("shogiquest", "quest")),
    ("kifudb", ("kifudb", "kifu-db", "kif-db")),
]
SOURCE_PREFIX_STRIP: dict[str, tuple[str, ...]] = {
    "wars": ("game", "gid", "wars", "wrs", "kifu", "record"),
    "81dojo": ("game", "record", "kifu"),
    "lishogi": ("game", "study", "analysis"),
    "shogiclub24": ("game", "kifu", "record"),
    "shogiquest": ("game", "record", "quest"),
}


def detect_game_id_source(raw: str) -> str:
    text = (raw or "").strip().lower()
    if not text:
        return "unknown"
    for name, needles in SOURCE_PATTERNS:
        if any(n in text for n in needles):
            return name
    return "unknown"


def _strip_noise(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.strip("\"'` ")
    s = unquote(s)
    s = re.sub(r"\.(kif|csa|txt|jsonl?|log)$", "", s, flags=re.IGNORECASE)
    s = re.split(r"[?#]", s, maxsplit=1)[0]
    return s.strip()


def _extract_from_url(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if "http://" not in s and "https://" not in s:
        return s

    parsed = urlparse(s)
    query = parse_qs(parsed.query)
    for key in KNOWN_QUERY_KEYS:
        vals = query.get(key)
        if vals:
            candidate = _strip_noise(vals[0])
            if candidate:
                return candidate

    path = parsed.path.strip("/")
    if path:
        segments = [seg for seg in path.split("/") if seg]
        for leaf in reversed(segments):
            candidate = _strip_noise(leaf)
            if candidate and candidate.lower() not in {"game", "kifu", "record", "view"}:
                return candidate
    return _strip_noise(s)


def normalize_game_id(raw: str, source_hint: str | None = None) -> str:
    s = _extract_from_url(raw)
    s = _strip_noise(s)
    if not s:
        return ""

    s = s.lower()
    s = s.replace("%2f", "/").replace("%3a", ":")
    s = re.sub(r"[\s\-_/.:]+", "", s)
    s = "".join(ch for ch in s if ch.isalnum())

    hint = (source_hint or "").strip().lower()
    if hint == "unknown":
        hint = detect_game_id_source(raw)
    for prefix in SOURCE_PREFIX_STRIP.get(hint, ()):
        if s.startswith(prefix) and len(s) > len(prefix) + 2:
            s = s[len(prefix) :]
            break
    s = re.sub(r"^(id|gid|kifid)", "", s)
    return s


__all__ = ["normalize_game_id", "detect_game_id_source"]
