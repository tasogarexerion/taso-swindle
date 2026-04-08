from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .mate_result import MateResult


DEFAULT_SOURCE_DETAIL_MAP: dict[str, str] = {
    "mate_for_us": "mate_for_us",
    "mate_for_them": "mate_for_them",
    "no_mate": "no_mate",
    "mate_hint": "mate_hint",
    "mated_hint": "mated_hint",
    "no_mate_hint": "no_mate_hint",
}


@dataclass(frozen=True)
class DfpnDialectPack:
    name: str
    strict_patterns: tuple[tuple[str, str, str], ...]
    loose_patterns: tuple[tuple[str, str, str], ...]
    distance_patterns: tuple[tuple[str, str], ...]
    negation_patterns: tuple[tuple[str, str], ...]
    source_detail_map: dict[str, str]
    priority: int


@dataclass
class DfPnResult:
    status: str = "unknown"
    mate_sign: str = "unknown"
    confidence: float = 0.0
    distance: Optional[int] = None
    parser_status: str = "unknown"
    source_detail: Optional[str] = None
    raw_summary: Optional[str] = None
    hits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dialect_used: str = "auto"
    dialect_candidates: list[str] = field(default_factory=list)


@dataclass
class DfpnPackValidationReport:
    path: str
    version: str = "unknown"
    valid_pack_names: list[str] = field(default_factory=list)
    invalid_pack_names: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


GENERIC_EN_PACK = DfpnDialectPack(
    name="generic_en",
    strict_patterns=(
        (r"\\bfor[_\\s-]?us\\b", "for_us", "mate_for_us"),
        (r"\\bmate[_\\s-]?for[_\\s-]?us\\b", "for_us", "mate_for_us"),
        (r"\\bin\\s+(\\d+)\\s+we\\s+mate\\b", "for_us", "mate_for_us"),
        (r"\\bfor[_\\s-]?them\\b", "for_them", "mate_for_them"),
        (r"\\bmate[_\\s-]?for[_\\s-]?them\\b", "for_them", "mate_for_them"),
        (r"\\bour[_\\s-]?king[_\\s-]?mated\\b", "for_them", "mate_for_them"),
        (r"\\bmated\\s+in\\s+(\\d+)\\b", "for_them", "mate_for_them"),
    ),
    loose_patterns=(
        (r"\\bmate\\b", "for_us", "mate_hint"),
        (r"\\bcheckmate\\b", "for_us", "mate_hint"),
        (r"\\bwin\\b", "for_us", "mate_hint"),
        (r"\\bfound\\b", "for_us", "mate_hint"),
        (r"\\bmated\\b", "for_them", "mated_hint"),
        (r"\\blose\\b", "for_them", "mated_hint"),
        (r"\\blost\\b", "for_them", "mated_hint"),
    ),
    distance_patterns=(
        (r"\\bin\\s+(\\d+)\\b", "in"),
        (r"\\bmate\\s+(\\d+)\\b", "mate"),
        (r"\\b(\\d+)\\s*ply\\b", "ply"),
        (r"\\b(\\d+)\\s*plies\\b", "plies"),
    ),
    negation_patterns=(
        (r"\\bnot[_\\s-]?found\\b", "no_mate"),
        (r"\\bno[_\\s-]?mate\\b", "no_mate"),
        (r"\\bmate[_\\s-]?none\\b", "no_mate"),
        (r"\\bunsolved\\b", "no_mate"),
        (r"\\bunknown\\b", "no_mate_hint"),
    ),
    source_detail_map=dict(DEFAULT_SOURCE_DETAIL_MAP),
    priority=30,
)

GENERIC_JA_PACK = DfpnDialectPack(
    name="generic_ja",
    strict_patterns=(
        (r"詰みあり", "for_us", "mate_for_us"),
        (r"詰み\\s*有り", "for_us", "mate_for_us"),
        (r"詰まし", "for_us", "mate_for_us"),
        (r"詰まされ", "for_them", "mate_for_them"),
    ),
    loose_patterns=(
        (r"詰み", "for_us", "mate_hint"),
        (r"勝ち", "for_us", "mate_hint"),
        (r"負け", "for_them", "mated_hint"),
        (r"不利", "for_them", "mated_hint"),
    ),
    distance_patterns=(
        (r"(\\d+)\\s*手", "te"),
        (r"\\b(\\d+)\\s*ply\\b", "ply"),
    ),
    negation_patterns=(
        (r"不詰", "no_mate"),
        (r"詰みなし", "no_mate"),
        (r"不明", "no_mate_hint"),
    ),
    source_detail_map=dict(DEFAULT_SOURCE_DETAIL_MAP),
    priority=20,
)

LEGACY_CLI_PACK = DfpnDialectPack(
    name="legacy_cli",
    strict_patterns=(
        (r"\\bresult\\s*[:=]\\s*win\\b", "for_us", "mate_for_us"),
        (r"\\bstatus\\s*[:=]\\s*mate\\b", "for_us", "mate_for_us"),
        (r"\\bresult\\s*[:=]\\s*lose\\b", "for_them", "mate_for_them"),
        (r"\\bstatus\\s*[:=]\\s*mated\\b", "for_them", "mate_for_them"),
    ),
    loose_patterns=(
        (r"\\bwin\\b", "for_us", "mate_hint"),
        (r"\\blose\\b", "for_them", "mated_hint"),
    ),
    distance_patterns=(
        (r"\\bply\\s*[:=]\\s*(\\d+)\\b", "ply"),
        (r"\\bmate\\s*[:=]\\s*(\\d+)\\b", "mate"),
    ),
    negation_patterns=(
        (r"\\bstatus\\s*[:=]\\s*nomate\\b", "no_mate"),
        (r"\\bresult\\s*[:=]\\s*unknown\\b", "no_mate_hint"),
    ),
    source_detail_map=dict(DEFAULT_SOURCE_DETAIL_MAP),
    priority=10,
)

COMPACT_PACK = DfpnDialectPack(
    name="compact",
    strict_patterns=(
        (r"\\bw\\+(\\d+)?\\b", "for_us", "mate_for_us"),
        (r"\\bl\\+(\\d+)?\\b", "for_them", "mate_for_them"),
        (r"\\bmate\\+\\s*(\\d+)\\b", "for_us", "mate_for_us"),
        (r"\\bmated\\+\\s*(\\d+)\\b", "for_them", "mate_for_them"),
    ),
    loose_patterns=(
        (r"\\bw\\b", "for_us", "mate_hint"),
        (r"\\bl\\b", "for_them", "mated_hint"),
    ),
    distance_patterns=(
        (r"[wl]\\+(\\d+)", "compact"),
        (r"\\+(\\d+)", "compact"),
    ),
    negation_patterns=(
        (r"\\bn\\b", "no_mate"),
        (r"\\b0\\b", "no_mate_hint"),
    ),
    source_detail_map=dict(DEFAULT_SOURCE_DETAIL_MAP),
    priority=5,
)


BUILTIN_DIALECT_PACKS: dict[str, DfpnDialectPack] = {
    "GENERIC_EN": GENERIC_EN_PACK,
    "GENERIC_JA": GENERIC_JA_PACK,
    "LEGACY_CLI": LEGACY_CLI_PACK,
    "COMPACT": COMPACT_PACK,
}


class DfPnAdapter:
    """df-pn process adapter with tolerant output parsing and dialect packs."""

    def __init__(
        self,
        path: str = "",
        parser_mode: str = "AUTO",
        dialect: str = "AUTO",
        dialect_pack_path: str = "",
    ) -> None:
        self.path = path
        self.parser_mode = _normalize_parser_mode(parser_mode)
        self.dialect = _normalize_dialect(dialect)
        self.dialect_pack_path = dialect_pack_path.strip()

        self._dialect_packs: dict[str, DfpnDialectPack] = dict(BUILTIN_DIALECT_PACKS)
        self._pack_source = "builtin"
        self._pack_version = "builtin-v1"
        self._pack_load_errors: list[str] = []
        self._reload_dialect_packs()

    def configure(
        self,
        *,
        path: str,
        parser_mode: str = "AUTO",
        dialect: str = "AUTO",
        dialect_pack_path: str = "",
    ) -> None:
        self.path = path.strip()
        self.parser_mode = _normalize_parser_mode(parser_mode)
        self.dialect = _normalize_dialect(dialect)
        self.dialect_pack_path = dialect_pack_path.strip()
        self._reload_dialect_packs()

    @property
    def pack_source(self) -> str:
        return self._pack_source

    @property
    def pack_version(self) -> str:
        return self._pack_version

    @property
    def pack_load_errors_count(self) -> int:
        return len(self._pack_load_errors)

    def available(self) -> bool:
        argv = self._command_argv()
        if not argv:
            return False
        exe = argv[0]
        return os.path.exists(exe) or bool(shutil_which(exe))

    def verify(
        self,
        *,
        root_position_cmd: str,
        move: str,
        timeout_ms: int,
        parser_mode: Optional[str] = None,
        dialect: Optional[str] = None,
    ) -> MateResult:
        notes: list[str] = []
        mode = _normalize_parser_mode(parser_mode or self.parser_mode)
        dialect_mode = _normalize_dialect(dialect or self.dialect)
        if not self.available():
            notes.append("dfpn_unavailable")
            return MateResult(
                found_mate=False,
                status="skipped",
                source="dfpn",
                engine_kind="dfpn",
                mate_sign="unknown",
                source_detail="dfpn:error:unavailable",
                notes=notes,
                dfpn_pack_source=self._pack_source,
                dfpn_pack_version=self._pack_version,
                dfpn_pack_load_errors=len(self._pack_load_errors),
            )
        if timeout_ms <= 0:
            notes.append("dfpn_timeout_ms<=0")
            return MateResult(
                found_mate=False,
                status="timeout",
                source="dfpn",
                engine_kind="dfpn",
                mate_sign="unknown",
                source_detail="dfpn:error:timeout",
                notes=notes,
                dfpn_pack_source=self._pack_source,
                dfpn_pack_version=self._pack_version,
                dfpn_pack_load_errors=len(self._pack_load_errors),
            )

        argv = self._command_argv()
        if not argv:
            notes.append("dfpn_unavailable")
            return MateResult(
                found_mate=False,
                status="skipped",
                source="dfpn",
                engine_kind="dfpn",
                mate_sign="unknown",
                source_detail="dfpn:error:unavailable",
                notes=notes,
                dfpn_pack_source=self._pack_source,
                dfpn_pack_version=self._pack_version,
                dfpn_pack_load_errors=len(self._pack_load_errors),
            )

        cmd = [*argv, "--position", root_position_cmd, "--move", move]
        try:
            completed = subprocess.run(  # noqa: S603,S607
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(0.05, timeout_ms / 1000.0),
            )
        except subprocess.TimeoutExpired:
            notes.append("dfpn_timeout")
            return MateResult(
                found_mate=False,
                status="timeout",
                source="dfpn",
                engine_kind="dfpn",
                mate_sign="unknown",
                source_detail="dfpn:error:timeout",
                notes=notes,
                dfpn_pack_source=self._pack_source,
                dfpn_pack_version=self._pack_version,
                dfpn_pack_load_errors=len(self._pack_load_errors),
            )
        except Exception:
            notes.append("dfpn_error")
            return MateResult(
                found_mate=False,
                status="error",
                source="dfpn",
                engine_kind="dfpn",
                mate_sign="unknown",
                source_detail="dfpn:error:parse_exception",
                notes=notes,
                dfpn_pack_source=self._pack_source,
                dfpn_pack_version=self._pack_version,
                dfpn_pack_load_errors=len(self._pack_load_errors),
            )

        out = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        if completed.returncode != 0 and not out:
            notes.append(f"dfpn_rc:{completed.returncode}")
            return MateResult(
                found_mate=False,
                status="error",
                source="dfpn",
                engine_kind="dfpn",
                mate_sign="unknown",
                source_detail="dfpn:error:empty_output",
                notes=notes,
                dfpn_pack_source=self._pack_source,
                dfpn_pack_version=self._pack_version,
                dfpn_pack_load_errors=len(self._pack_load_errors),
            )

        parsed = _parse_output(
            out,
            mode=mode,
            dialect=dialect_mode,
            packs=self._dialect_packs,
            pack_source=self._pack_source,
        )
        notes.extend(parsed.notes)
        for hit in parsed.hits:
            notes.append(f"dfpn_hit:{hit}")

        if completed.returncode != 0 and parsed.status == "unknown":
            notes.append(f"dfpn_rc:{completed.returncode}")
            parsed.status = "error"
            parsed.parser_status = "error"
            parsed.source_detail = "dfpn:error:nonzero_exit"

        return MateResult(
            found_mate=(parsed.mate_sign == "for_us" and parsed.status == "confirmed"),
            mate_in=parsed.distance,
            distance=parsed.distance,
            confidence=_clamp(parsed.confidence),
            source="dfpn",
            status=parsed.status,
            engine_kind="dfpn",
            mate_sign=parsed.mate_sign,
            source_detail=parsed.source_detail,
            raw_summary=parsed.raw_summary,
            dfpn_dialect_used=parsed.dialect_used,
            dfpn_dialect_candidates=list(parsed.dialect_candidates),
            dfpn_source_detail_normalized=parsed.source_detail,
            dfpn_pack_source=self._pack_source,
            dfpn_pack_version=self._pack_version,
            dfpn_pack_load_errors=len(self._pack_load_errors),
            notes=notes,
        )

    def _reload_dialect_packs(self) -> None:
        selected_path = self._select_dialect_pack_path()
        if selected_path is None:
            self._dialect_packs = dict(BUILTIN_DIALECT_PACKS)
            self._pack_source = "builtin"
            self._pack_version = "builtin-v1"
            self._pack_load_errors = []
            return

        packs, version, errors = _load_external_packs(selected_path)
        if packs:
            self._dialect_packs = packs
            self._pack_source = "external"
            self._pack_version = version
            self._pack_load_errors = list(errors)
            return

        self._dialect_packs = dict(BUILTIN_DIALECT_PACKS)
        self._pack_source = "external_fallback_builtin"
        self._pack_version = version if version else "builtin-v1"
        self._pack_load_errors = list(errors)

    def _select_dialect_pack_path(self) -> Optional[Path]:
        explicit = self.dialect_pack_path.strip()
        if explicit:
            p = Path(explicit).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            return p

        default_path = Path(__file__).resolve().parents[2] / "dfpn_dialects" / "default_packs.json"
        if default_path.exists():
            return default_path
        return None

    def _command_argv(self) -> list[str]:
        raw = self.path.strip()
        if not raw:
            return []
        try:
            return shlex.split(raw)
        except Exception:
            return []


def validate_dialect_pack_file(path: str) -> DfpnPackValidationReport:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()

    report = DfpnPackValidationReport(path=str(p))
    packs, version, errors = _load_external_packs(p)
    report.version = version if version else "unknown"
    report.errors.extend(errors)

    if not p.exists():
        report.errors.append("file_not_found")
        return report

    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        raw_packs = payload.get("packs", []) if isinstance(payload, dict) else []
    except Exception:
        report.errors.append("json_load_error")
        return report

    good_keys = set(packs.keys())
    if isinstance(raw_packs, list):
        for raw in raw_packs:
            if not isinstance(raw, dict):
                report.invalid_pack_names.append("<non_dict_pack>")
                continue
            name = str(raw.get("name", "<unnamed>")).strip() or "<unnamed>"
            key = _pack_key(name)
            if key in good_keys:
                report.valid_pack_names.append(name)
            else:
                report.invalid_pack_names.append(name)

    return report


def _normalize_parser_mode(mode: str) -> str:
    m = (mode or "AUTO").strip().upper()
    if m not in {"AUTO", "STRICT", "LOOSE"}:
        return "AUTO"
    return m


def _normalize_dialect(dialect: str) -> str:
    d = (dialect or "AUTO").strip().upper()
    if not d:
        return "AUTO"
    return d


def _parse_output(
    text: str,
    *,
    mode: str,
    dialect: str,
    packs: dict[str, DfpnDialectPack],
    pack_source: str,
) -> DfPnResult:
    out = (text or "").strip()
    raw_summary = _short_summary(out)

    candidates = _candidate_packs(dialect, packs)
    best_partial: Optional[DfPnResult] = None
    best_unknown: Optional[DfPnResult] = None
    for pack in candidates:
        parsed = _parse_with_pack(out, mode=mode, pack=pack, dialect=dialect)
        parsed.dialect_candidates = [p.name for p in candidates]
        if parsed.parser_status == "ok":
            return parsed
        if parsed.parser_status == "partial":
            if best_partial is None or parsed.confidence > best_partial.confidence:
                best_partial = parsed
            continue
        if best_unknown is None or parsed.confidence > best_unknown.confidence:
            best_unknown = parsed

    if best_partial is not None:
        if best_partial.raw_summary is None:
            best_partial.raw_summary = raw_summary
        return best_partial

    used = dialect.lower() if dialect != "AUTO" else "auto"
    if best_unknown is None:
        best_unknown = DfPnResult(
            status="unknown",
            mate_sign="unknown",
            confidence=0.0,
            parser_status="unknown",
            source_detail=f"dfpn:{used}:unknown_format",
            raw_summary=raw_summary,
            dialect_used=used,
            dialect_candidates=[p.name for p in candidates],
            notes=["dfpn_parser:unknown", "dfpn_parse_unknown"],
        )
    if best_unknown.raw_summary is None:
        best_unknown.raw_summary = raw_summary
    if pack_source == "external_fallback_builtin":
        best_unknown.notes.append("dfpn_pack_fallback_builtin")
    return best_unknown


def _candidate_packs(dialect: str, packs: dict[str, DfpnDialectPack]) -> list[DfpnDialectPack]:
    if dialect != "AUTO":
        key = _pack_key(dialect)
        if key in packs:
            return [packs[key]]

    ordered = list(packs.values())
    ordered.sort(key=lambda p: p.priority, reverse=True)
    return ordered


def _parse_with_pack(
    text: str,
    *,
    mode: str,
    pack: DfpnDialectPack,
    dialect: str,
) -> DfPnResult:
    lower = text.lower()
    hits: list[str] = []
    notes: list[str] = []

    distance, distance_hit = _extract_distance(text, pack.distance_patterns)
    if distance_hit is not None:
        hits.append(f"{pack.name}:distance:{distance_hit}")

    negation = _match_negation(lower, pack.negation_patterns)
    strict = _match_group(lower, pack.strict_patterns)
    loose = _match_group(lower, pack.loose_patterns)

    parser_status = "unknown"
    sign = "unknown"
    status = "unknown"
    base_token = "unknown_format"
    source_mode = "unknown"
    confidence = 0.0

    if negation is not None:
        parser_status = "ok"
        status = "unknown"
        sign = "unknown"
        base_token = pack.source_detail_map.get(negation, "no_mate")
        source_mode = "strict"
        hits.append(f"{pack.name}:strict:{base_token}")
        confidence = 0.33
    elif strict is not None:
        sign = strict[0]
        status = "confirmed" if sign == "for_us" else "rejected"
        parser_status = "ok"
        base_token = pack.source_detail_map.get(strict[1], strict[1])
        source_mode = "strict"
        hits.append(f"{pack.name}:strict:{base_token}")
        confidence = 0.76
    elif mode != "STRICT" and loose is not None:
        sign = loose[0]
        status = "confirmed" if sign == "for_us" else "rejected"
        parser_status = "partial" if mode == "AUTO" else "ok"
        base_token = pack.source_detail_map.get(loose[1], loose[1])
        source_mode = "loose"
        hits.append(f"{pack.name}:loose:{base_token}")
        confidence = 0.45

    if distance is not None and sign in {"for_us", "for_them"}:
        confidence += 0.10
    if parser_status == "ok" and sign in {"for_us", "for_them"} and distance is not None:
        confidence = max(confidence, 0.86)

    dialect_used = pack.name
    if dialect == "AUTO" and parser_status == "unknown":
        dialect_used = "auto"

    if source_mode != "unknown":
        source_detail = f"dfpn:{dialect_used}:{source_mode}:{base_token}"
    else:
        source_detail = f"dfpn:{dialect_used}:unknown_format"

    notes.append(f"dfpn_parser:{parser_status}")
    if status == "unknown":
        notes.append("dfpn_parse_unknown")
    elif status == "confirmed":
        notes.append("dfpn_for_us")
    elif status == "rejected":
        notes.append("dfpn_for_them")

    return DfPnResult(
        status=status,
        mate_sign=sign,
        confidence=_clamp(confidence),
        distance=distance,
        parser_status=parser_status,
        source_detail=source_detail,
        raw_summary=_short_summary(text),
        hits=hits,
        notes=notes,
        dialect_used=dialect_used,
    )


def _load_external_packs(path: Path) -> tuple[dict[str, DfpnDialectPack], str, list[str]]:
    errors: list[str] = []
    if not path.exists():
        errors.append(f"path_not_found:{path}")
        return {}, "unknown", errors

    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as exc:
        errors.append(f"json_load_error:{exc.__class__.__name__}")
        return {}, "unknown", errors

    if not isinstance(payload, dict):
        errors.append("payload_not_object")
        return {}, "unknown", errors

    version = str(payload.get("version", "unknown") or "unknown")
    raw_packs = payload.get("packs")
    if not isinstance(raw_packs, list):
        errors.append("packs_not_list")
        return {}, version, errors

    packs: dict[str, DfpnDialectPack] = {}
    for idx, raw in enumerate(raw_packs):
        pack, pack_errors = _parse_pack_entry(raw, idx)
        if pack_errors:
            errors.extend(pack_errors)
        if pack is None:
            continue
        key = _pack_key(pack.name)
        packs[key] = pack

    return packs, version, errors


def _parse_pack_entry(raw: object, index: int) -> tuple[Optional[DfpnDialectPack], list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, [f"pack[{index}]:not_object"]

    name = str(raw.get("name", "")).strip()
    if not name:
        errors.append(f"pack[{index}]:missing_name")
        return None, errors

    try:
        priority = int(raw.get("priority", 0))
    except Exception:
        errors.append(f"pack[{name}]:invalid_priority")
        return None, errors

    strict = _parse_strict_or_loose(raw.get("strict_patterns"), name=name, key="strict_patterns")
    loose = _parse_strict_or_loose(raw.get("loose_patterns"), name=name, key="loose_patterns")
    distance = _parse_pair_patterns(raw.get("distance_patterns"), name=name, key="distance_patterns")
    negation = _parse_pair_patterns(raw.get("negation_patterns"), name=name, key="negation_patterns")

    for maybe_errs in (strict[1], loose[1], distance[1], negation[1]):
        errors.extend(maybe_errs)

    if errors:
        return None, errors

    source_map = raw.get("source_detail_map")
    if isinstance(source_map, dict):
        normalized_map: dict[str, str] = dict(DEFAULT_SOURCE_DETAIL_MAP)
        for k, v in source_map.items():
            if isinstance(k, str) and isinstance(v, str) and k.strip() and v.strip():
                normalized_map[k.strip()] = v.strip()
    else:
        normalized_map = dict(DEFAULT_SOURCE_DETAIL_MAP)

    pack = DfpnDialectPack(
        name=name,
        strict_patterns=tuple(strict[0]),
        loose_patterns=tuple(loose[0]),
        distance_patterns=tuple(distance[0]),
        negation_patterns=tuple(negation[0]),
        source_detail_map=normalized_map,
        priority=priority,
    )
    return pack, []


def _parse_strict_or_loose(raw: object, *, name: str, key: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    errors: list[str] = []
    out: list[tuple[str, str, str]] = []
    if not isinstance(raw, list):
        return [], [f"pack[{name}]:{key}_not_list"]
    for idx, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            errors.append(f"pack[{name}]:{key}[{idx}]_bad_format")
            continue
        pattern, sign, token = item
        if not isinstance(pattern, str) or not isinstance(sign, str) or not isinstance(token, str):
            errors.append(f"pack[{name}]:{key}[{idx}]_non_string")
            continue
        try:
            re.compile(pattern)
        except re.error:
            errors.append(f"pack[{name}]:{key}[{idx}]_regex_error")
            continue
        out.append((pattern, sign, token))
    if errors:
        return [], errors
    return out, []


def _parse_pair_patterns(raw: object, *, name: str, key: str) -> tuple[list[tuple[str, str]], list[str]]:
    errors: list[str] = []
    out: list[tuple[str, str]] = []
    if not isinstance(raw, list):
        return [], [f"pack[{name}]:{key}_not_list"]
    for idx, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            errors.append(f"pack[{name}]:{key}[{idx}]_bad_format")
            continue
        pattern, token = item
        if not isinstance(pattern, str) or not isinstance(token, str):
            errors.append(f"pack[{name}]:{key}[{idx}]_non_string")
            continue
        try:
            re.compile(pattern)
        except re.error:
            errors.append(f"pack[{name}]:{key}[{idx}]_regex_error")
            continue
        out.append((pattern, token))
    if errors:
        return [], errors
    return out, []


def _pack_key(name: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", (name or "").strip().upper())
    return key.strip("_") or "PACK"


def _match_group(text: str, patterns: tuple[tuple[str, str, str], ...]) -> Optional[tuple[str, str]]:
    for pattern, sign, token in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return sign, token
    return None


def _match_negation(text: str, patterns: tuple[tuple[str, str], ...]) -> Optional[str]:
    for pattern, token in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return token
    return None


def _extract_distance(text: str, patterns: tuple[tuple[str, str], ...]) -> tuple[Optional[int], Optional[str]]:
    for pattern, token in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if not m:
            continue
        try:
            value = int(m.group(1))
        except Exception:
            continue
        if value >= 0:
            return value, token
    return None, None


def _short_summary(text: str, limit: int = 160) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit]


def shutil_which(exe: str) -> Optional[str]:
    try:
        import shutil

        return shutil.which(exe)
    except Exception:
        return None


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
