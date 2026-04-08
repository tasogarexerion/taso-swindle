from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.swindle.features.trap_features import compute_human_trap_score
from taso_swindle.swindle.reply_search import ReplyEval


def _r(move: str, u: float, *, flashy: bool = False, check_like: bool = False) -> ReplyEval:
    return ReplyEval(
        move=move,
        multipv=1,
        pv=[move],
        cp_raw=int(u),
        mate_raw=None,
        opp_utility=u,
        root_cp=-int(u),
        root_mate=None,
        is_check_like=check_like,
        is_flashy_like=flashy,
    )


def test_trap_not_zero() -> None:
    replies = [_r("R*2b", 500, flashy=True, check_like=True), _r("7g7f", 80), _r("2g2f", -250)]
    score = compute_human_trap_score(replies, gap12=420, gap13=700)
    assert score > 0.0


if __name__ == "__main__":
    test_trap_not_zero()
    print("ok test_trap_features")
