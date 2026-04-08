from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.dfpn_adapter import DfPnAdapter


def test_dfpn_parse_mate_for_us_with_distance() -> None:
    adapter = DfPnAdapter("/bin/echo for_us mate in 7")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="AUTO")
    assert result.status == "confirmed"
    assert result.mate_sign == "for_us"
    assert result.distance == 7
    assert result.confidence > 0.0


def test_dfpn_parse_mate_for_them_with_distance() -> None:
    adapter = DfPnAdapter("/bin/echo for_them mated in 5")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="AUTO")
    assert result.status == "rejected"
    assert result.mate_sign == "for_them"
    assert result.distance == 5


def test_dfpn_parse_unknown_format_returns_unknown() -> None:
    adapter = DfPnAdapter("/bin/echo mystery_payload")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="AUTO")
    assert result.status == "unknown"
    assert result.mate_sign == "unknown"
    assert result.raw_summary is not None


def test_dfpn_parser_strict_vs_loose() -> None:
    adapter = DfPnAdapter("/bin/echo win in 6")
    strict = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    loose = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="LOOSE")
    assert strict.status == "unknown"
    assert loose.status == "confirmed"
    assert loose.distance == 6


if __name__ == "__main__":
    test_dfpn_parse_mate_for_us_with_distance()
    test_dfpn_parse_mate_for_them_with_distance()
    test_dfpn_parse_unknown_format_returns_unknown()
    test_dfpn_parser_strict_vs_loose()
    print("ok test_dfpn_parser_phase5")
