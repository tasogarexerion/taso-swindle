from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.swindle.features.onlymove_features import compute_onlymove_pressure
from taso_swindle.swindle.reply_search import ReplyEval


def _r(move: str, u: float, mate: int | None = None) -> ReplyEval:
    return ReplyEval(
        move=move,
        multipv=1,
        pv=[move],
        cp_raw=int(u) if mate is None else None,
        mate_raw=mate,
        opp_utility=u,
        root_cp=-int(u) if mate is None else None,
        root_mate=(-mate if mate is not None else None),
        is_check_like=False,
        is_flashy_like=False,
    )


def test_gap12_gap13_basic() -> None:
    score, g12, g13 = compute_onlymove_pressure([_r("7g7f", 500), _r("2g2f", 200), _r("8h2b+", 50)])
    assert g12 == 300
    assert g13 == 450
    assert 0.0 < score <= 1.0


def test_missing_reply_is_conservative() -> None:
    score, g12, g13 = compute_onlymove_pressure([_r("7g7f", 500)])
    assert g12 == 0.0
    assert g13 == 0.0
    assert score == 0.0


def test_mate_mixed_handling() -> None:
    score, g12, _ = compute_onlymove_pressure([_r("7g7f", 110_000, mate=10), _r("2g2f", 200)])
    assert g12 > 100_000
    assert score > 0.3


if __name__ == "__main__":
    test_gap12_gap13_basic()
    test_missing_reply_is_conservative()
    test_mate_mixed_handling()
    print("ok test_onlymove_gap")
