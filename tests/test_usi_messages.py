from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.usi_messages import (
    is_special_bestmove,
    is_usi_move_token,
    parse_mate_token,
    parse_setoption,
)


def test_parse_setoption_with_spaces() -> None:
    cmd = parse_setoption("setoption name BackendEnginePath value /tmp/My Engine")
    assert cmd is not None
    assert cmd.name == "BackendEnginePath"
    assert cmd.value == "/tmp/My Engine"


def test_parse_setoption_without_value() -> None:
    cmd = parse_setoption("setoption name SwindleEnable")
    assert cmd is not None
    assert cmd.name == "SwindleEnable"
    assert cmd.value == ""


def test_parse_mate_token_plus_minus() -> None:
    assert parse_mate_token("+7") == 7
    assert parse_mate_token("-3") == -3
    assert parse_mate_token("0") == 0


def test_bestmove_special_and_usi_move_boundaries() -> None:
    assert is_special_bestmove("resign")
    assert is_special_bestmove("0000")

    assert is_usi_move_token("7g7f")
    assert is_usi_move_token("7g7f+")
    assert is_usi_move_token("P*7f")

    assert not is_usi_move_token("resign")
    assert not is_usi_move_token("0000")
    assert not is_usi_move_token("invalid")


if __name__ == "__main__":
    test_parse_setoption_with_spaces()
    test_parse_setoption_without_value()
    test_parse_mate_token_plus_minus()
    test_bestmove_special_and_usi_move_boundaries()
    print("ok test_usi_messages")
