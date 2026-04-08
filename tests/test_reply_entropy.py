import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.swindle.features.entropy_features import compute_reply_entropy
from taso_swindle.swindle.reply_search import ReplyEval


def _r(move: str, u: float) -> ReplyEval:
    return ReplyEval(
        move=move,
        multipv=1,
        pv=[move],
        cp_raw=int(u),
        mate_raw=None,
        opp_utility=u,
        root_cp=-int(u),
        root_mate=None,
        is_check_like=False,
        is_flashy_like=False,
    )


def test_uniform_distribution_entropy_high() -> None:
    score, entropy = compute_reply_entropy([_r("7g7f", 100), _r("2g2f", 100), _r("8h2b+", 100)])
    assert score > 0.95
    assert entropy > 0.95


def test_skewed_distribution_entropy_low() -> None:
    score, entropy = compute_reply_entropy([_r("7g7f", 2000), _r("2g2f", 50), _r("8h2b+", 10)])
    assert score < 0.6
    assert entropy < 0.6


def test_nan_guard() -> None:
    score, entropy = compute_reply_entropy([_r("7g7f", 10**9), _r("2g2f", -10**9)])
    assert math.isfinite(score)
    assert math.isfinite(entropy)


if __name__ == "__main__":
    test_uniform_distribution_entropy_high()
    test_skewed_distribution_entropy_low()
    test_nan_guard()
    print("ok test_reply_entropy")
