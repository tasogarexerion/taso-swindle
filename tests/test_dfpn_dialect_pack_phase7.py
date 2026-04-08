from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.dfpn_adapter import DfPnAdapter


def test_auto_selects_japanese_pack() -> None:
    adapter = DfPnAdapter("/bin/echo 詰みあり 7手", parser_mode="AUTO", dialect="AUTO")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.status == "confirmed"
    assert result.mate_sign == "for_us"
    assert result.dfpn_dialect_used == "generic_ja"


def test_auto_selects_legacy_pack() -> None:
    adapter = DfPnAdapter("/bin/echo result=win ply=5", parser_mode="AUTO", dialect="AUTO")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.status == "confirmed"
    assert result.mate_sign == "for_us"
    assert result.dfpn_dialect_used == "legacy_cli"


def test_forced_dialect_uses_requested_pack() -> None:
    adapter = DfPnAdapter("/bin/echo for_us mate in 9", parser_mode="STRICT", dialect="GENERIC_EN")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.status == "confirmed"
    assert result.dfpn_dialect_used == "generic_en"


def test_source_detail_contains_dialect_and_mode() -> None:
    adapter = DfPnAdapter("/bin/echo 詰みあり 9手", parser_mode="STRICT", dialect="GENERIC_JA")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.source_detail is not None
    assert result.source_detail.startswith("dfpn:generic_ja:strict:")
    assert result.dfpn_source_detail_normalized == result.source_detail


def test_unknown_dialect_falls_back_auto() -> None:
    adapter = DfPnAdapter("/bin/echo 詰みあり 5手", parser_mode="AUTO", dialect="UNKNOWN_DIALECT")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.status == "confirmed"
    assert result.dfpn_dialect_used in {"generic_ja", "generic_en", "legacy_cli", "compact"}


def test_parser_failure_still_returns_unknown() -> None:
    adapter = DfPnAdapter("/bin/echo mystery_payload_without_tokens", parser_mode="STRICT", dialect="AUTO")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100)
    assert result.status == "unknown"
    assert result.mate_sign == "unknown"
    assert result.source_detail is not None
    assert result.source_detail.startswith("dfpn:")


if __name__ == "__main__":
    test_auto_selects_japanese_pack()
    test_auto_selects_legacy_pack()
    test_forced_dialect_uses_requested_pack()
    test_source_detail_contains_dialect_and_mode()
    test_unknown_dialect_falls_back_auto()
    test_parser_failure_still_returns_unknown()
    print("ok test_dfpn_dialect_pack_phase7")
