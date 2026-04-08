from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.mate.dfpn_adapter import DfPnAdapter
from taso_swindle.swindle.context import SwindleContext
from taso_swindle.swindle.controller import Stage1Decision
from taso_swindle.usi_protocol import USIProtocol


def _write_pack(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _minimal_external_pack() -> dict:
    return {
        "version": "test-v1",
        "packs": [
            {
                "name": "generic_en",
                "priority": 50,
                "strict_patterns": [[r"\bfor[_\s-]?us\b", "for_us", "mate_for_us"]],
                "loose_patterns": [[r"\bwin\b", "for_us", "mate_hint"]],
                "distance_patterns": [[r"\bin\s+(\d+)\b", "in"]],
                "negation_patterns": [[r"\bno[_\s-]?mate\b", "no_mate"]],
            }
        ],
    }


def test_external_pack_load_success() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-pack-") as td:
        p = Path(td) / "packs.json"
        _write_pack(p, _minimal_external_pack())
        adapter = DfPnAdapter("/bin/echo for_us in 7", parser_mode="STRICT", dialect_pack_path=str(p))
        result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
        assert result.status == "confirmed"
        assert result.dfpn_pack_source == "external"
        assert result.dfpn_pack_version == "test-v1"
        assert result.dfpn_pack_load_errors == 0


def test_external_pack_invalid_regex_partial_fallback() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-pack-") as td:
        p = Path(td) / "packs.json"
        payload = _minimal_external_pack()
        payload["packs"].append(
            {
                "name": "broken_pack",
                "priority": 1,
                "strict_patterns": [[r"(unclosed", "for_us", "mate_for_us"]],
                "loose_patterns": [],
                "distance_patterns": [],
                "negation_patterns": [],
            }
        )
        _write_pack(p, payload)

        adapter = DfPnAdapter("/bin/echo for_us in 6", parser_mode="STRICT", dialect_pack_path=str(p))
        result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
        assert result.status == "confirmed"
        assert result.dfpn_pack_source == "external"
        assert result.dfpn_pack_load_errors > 0


def test_external_pack_all_invalid_falls_back_builtin() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-pack-") as td:
        p = Path(td) / "packs.json"
        payload = {
            "version": "broken-v1",
            "packs": [
                {
                    "name": "broken_only",
                    "priority": 1,
                    "strict_patterns": [[r"(bad", "for_us", "mate_for_us"]],
                    "loose_patterns": [],
                    "distance_patterns": [],
                    "negation_patterns": [],
                }
            ],
        }
        _write_pack(p, payload)
        adapter = DfPnAdapter("/bin/echo 詰みあり 5手", parser_mode="STRICT", dialect_pack_path=str(p))
        result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
        assert result.status == "confirmed"
        assert result.dfpn_pack_source == "external_fallback_builtin"
        assert result.dfpn_pack_load_errors > 0


def test_forced_pack_path_missing_falls_back_builtin() -> None:
    missing = ROOT / "dfpn_dialects" / "not-found-pack.json"
    adapter = DfPnAdapter("/bin/echo 詰みあり 5手", parser_mode="STRICT", dialect_pack_path=str(missing))
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.status == "confirmed"
    assert result.dfpn_pack_source == "external_fallback_builtin"


def test_event_logs_pack_source_and_version() -> None:
    cfg = SwindleConfig()
    proto = USIProtocol(cfg)
    captured = []
    proto.logger.log_decision = lambda event: captured.append(event)  # type: ignore[assignment]

    decision = Stage1Decision(
        normal_bestmove="7g7f",
        selected_move="7g7f",
        selected_reason="rev_max",
        candidates=[],
        activated=True,
        mode="HYBRID",
        dfpn_pack_source="external",
        dfpn_pack_version="test-v2",
        dfpn_pack_load_errors=2,
    )
    ctx = SwindleContext(
        side_to_move="b",
        root_sfen="startpos",
        root_position_cmd="position startpos",
        root_eval_cp=-500,
        root_mate_score=None,
        is_losing=True,
        is_lost=False,
        time_left_ms=5000,
        byoyomi_ms=1000,
        increment_ms=0,
        mode="HYBRID",
        swindle_enabled=True,
        emergency_fast_mode=False,
        dynamic_drop_cap_cp=600,
    )
    proto._emit_log(
        decision=decision,
        context=ctx,
        go_time_info={"movetime": 200},
        search_id=1,
        normal_bestmove="7g7f",
        final_bestmove="7g7f",
        selected_reason="rev_max",
        backend_restart_count=0,
    )
    assert captured
    event = captured[-1]
    assert event.dfpn_pack_source == "external"
    assert event.dfpn_pack_version == "test-v2"
    assert event.dfpn_pack_load_errors == 2


def test_source_detail_normalization_still_works() -> None:
    adapter = DfPnAdapter("/bin/echo for_us in 11", parser_mode="STRICT", dialect="GENERIC_EN")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.source_detail is not None
    assert result.source_detail.startswith("dfpn:")
    assert result.dfpn_source_detail_normalized == result.source_detail


if __name__ == "__main__":
    test_external_pack_load_success()
    test_external_pack_invalid_regex_partial_fallback()
    test_external_pack_all_invalid_falls_back_builtin()
    test_forced_pack_path_missing_falls_back_builtin()
    test_event_logs_pack_source_and_version()
    test_source_detail_normalization_still_works()
    print("ok test_dfpn_dialect_external_phase8")
