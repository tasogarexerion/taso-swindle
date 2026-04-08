from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.swindle.candidate import CandidateMove
from taso_swindle.swindle.context import SwindleContext
from taso_swindle.swindle.modes import MODE_HYBRID, MODE_MURKY, MODE_TACTICAL, resolve_mode


def _ctx() -> SwindleContext:
    return SwindleContext(
        side_to_move="b",
        root_sfen="startpos",
        root_position_cmd="position startpos",
        root_eval_cp=-900,
        root_mate_score=None,
        is_losing=True,
        is_lost=False,
        time_left_ms=10000,
        byoyomi_ms=1000,
        increment_ms=0,
        mode="AUTO",
        swindle_enabled=True,
        emergency_fast_mode=False,
        dynamic_drop_cap_cp=800,
    )


def test_auto_tactical_from_check_like() -> None:
    ctx = _ctx()
    cands = [
        CandidateMove(move="8h2b+", pv=["8h2b+"], base_cp=-300, mate_score=None, depth=10),
        CandidateMove(move="R*2b", pv=["R*2b"], base_cp=-320, mate_score=None, depth=10),
        CandidateMove(move="7g7f", pv=["7g7f"], base_cp=-350, mate_score=None, depth=10),
    ]
    assert resolve_mode("AUTO", ctx, cands) == MODE_TACTICAL


def test_auto_murky_from_tight_cp_spread() -> None:
    ctx = _ctx()
    cands = [
        CandidateMove(move="7g7f", pv=["7g7f"], base_cp=-500, mate_score=None, depth=10),
        CandidateMove(move="2g2f", pv=["2g2f"], base_cp=-530, mate_score=None, depth=10),
        CandidateMove(move="3g3f", pv=["3g3f"], base_cp=-550, mate_score=None, depth=10),
    ]
    assert resolve_mode("AUTO", ctx, cands, entropy_hint=0.7) == MODE_MURKY


def test_auto_hybrid_fallback() -> None:
    ctx = _ctx()
    cands = [CandidateMove(move="7g7f", pv=["7g7f"], base_cp=-1200, mate_score=None, depth=10)]
    assert resolve_mode("AUTO", ctx, cands, entropy_hint=0.1) == MODE_HYBRID


if __name__ == "__main__":
    test_auto_tactical_from_check_like()
    test_auto_murky_from_tight_cp_spread()
    test_auto_hybrid_fallback()
    print("ok test_modes")
