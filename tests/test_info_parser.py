from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.info_parser import InfoParseResult, InfoParser


def test_parse_basic_cp_pv_multipv() -> None:
    parser = InfoParser()
    line = "info depth 18 multipv 2 score cp -123 nodes 999 nps 10000 hashfull 123 time 456 pv 7g7f 3c3d"
    snap = parser.parse_line(line)

    assert snap is not None
    assert snap.depth == 18
    assert snap.multipv == 2
    assert snap.cp == -123
    assert snap.move == "7g7f"
    assert snap.nodes == 999
    assert snap.nps == 10000
    assert snap.hashfull == 123
    assert snap.time_ms == 456


def test_parse_mate_priority() -> None:
    parser = InfoParser()
    line = "info depth 20 multipv 1 score mate 5 pv 2b3c+ 8h2b"
    snap = parser.parse_line(line)

    assert snap is not None
    assert snap.mate == 5
    assert snap.cp == 30000
    assert snap.move == "2b3c+"


def test_same_multipv_overwrite() -> None:
    parser = InfoParser()
    result = InfoParseResult()

    line1 = "info depth 10 multipv 1 score cp -500 pv 7g7f"
    line2 = "info depth 14 multipv 1 score cp -320 pv 2g2f"

    snap1 = parser.parse_line(line1)
    snap2 = parser.parse_line(line2)
    assert snap1 is not None
    assert snap2 is not None

    result.upsert(snap1)
    result.upsert(snap2)

    assert result.by_multipv[1].depth == 14
    assert result.by_multipv[1].move == "2g2f"


def test_invalid_line_is_skipped() -> None:
    parser = InfoParser()
    assert parser.parse_line("bestmove 7g7f") is None
    assert parser.parse_line("info nonsense token") is None


if __name__ == "__main__":
    test_parse_basic_cp_pv_multipv()
    test_parse_mate_priority()
    test_same_multipv_overwrite()
    test_invalid_line_is_skipped()
    print("ok test_info_parser")
