from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.dfpn_adapter import DfPnAdapter


def test_parse_strict_for_us_japanese_text() -> None:
    adapter = DfPnAdapter("/bin/echo 詰みあり 7手")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    assert result.status == "confirmed"
    assert result.mate_sign == "for_us"
    assert result.distance == 7
    assert result.source_detail is not None
    assert result.source_detail.endswith(":strict:mate_for_us")


def test_parse_strict_for_them_english_text() -> None:
    adapter = DfPnAdapter("/bin/echo mated in 9")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    assert result.status == "rejected"
    assert result.mate_sign == "for_them"
    assert result.distance == 9
    assert result.source_detail is not None
    assert result.source_detail.endswith(":strict:mate_for_them")


def test_parse_loose_hint_mode_auto() -> None:
    adapter = DfPnAdapter("/bin/echo win likely")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="AUTO")
    assert result.status == "confirmed"
    assert result.mate_sign == "for_us"
    assert result.source_detail is not None
    assert result.source_detail.endswith(":loose:mate_hint")


def test_parse_strict_rejects_loose() -> None:
    adapter = DfPnAdapter("/bin/echo win likely")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    assert result.status == "unknown"
    assert result.mate_sign == "unknown"
    assert result.source_detail is not None
    assert result.source_detail.endswith(":unknown_format")


def test_distance_extract_in_ply() -> None:
    adapter = DfPnAdapter("/bin/echo for_us in 11 ply")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    assert result.distance == 11
    assert any(":distance:in" in note or ":distance:ply" in note for note in result.notes)


def test_distance_extract_in_te() -> None:
    adapter = DfPnAdapter("/bin/echo 詰みあり 13手")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    assert result.distance == 13
    assert any(":distance:te" in note for note in result.notes)


def test_unknown_format_returns_unknown_with_summary() -> None:
    adapter = DfPnAdapter("/bin/echo mystery_payload_without_known_tokens")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="AUTO")
    assert result.status == "unknown"
    assert result.raw_summary is not None
    assert "mystery_payload" in result.raw_summary


def test_confidence_clamped() -> None:
    adapter = DfPnAdapter("/bin/echo for_us in 1 ply")
    result = adapter.verify(root_position_cmd="position startpos", move="7g7f", timeout_ms=100, parser_mode="STRICT")
    assert 0.0 <= result.confidence <= 1.0


if __name__ == "__main__":
    test_parse_strict_for_us_japanese_text()
    test_parse_strict_for_them_english_text()
    test_parse_loose_hint_mode_auto()
    test_parse_strict_rejects_loose()
    test_distance_extract_in_ply()
    test_distance_extract_in_te()
    test_unknown_format_returns_unknown_with_summary()
    test_confidence_clamped()
    print("ok test_dfpn_parser_phase6")
