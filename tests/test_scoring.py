from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.swindle.candidate import CandidateMove
from taso_swindle.swindle.modes import MODE_MURKY, MODE_TACTICAL, mode_weight_scale
from taso_swindle.swindle.scoring import RevWeights, compute_rev_score


def _candidate() -> CandidateMove:
    c = CandidateMove(move="7g7f", pv=["7g7f"], base_cp=-400, mate_score=None, depth=14)
    c.features.mate_urgency = 0.1
    c.features.threat_score = 0.4
    c.features.only_move_pressure = 0.7
    c.features.reply_entropy_score = 0.6
    c.features.human_trap_score = 0.5
    c.features.self_risk = 0.2
    c.features.survival_score = 0.8
    c.features.pseudo_hisshi_score = 0.3
    return c


def test_rev_breakdown_matches_total() -> None:
    config = SwindleConfig()
    c = _candidate()
    w = RevWeights.from_config(config, mode_weight_scale(MODE_TACTICAL))
    total, breakdown = compute_rev_score(c, w)
    assert abs(total - breakdown.total) < 1e-9


def test_mode_difference_tactical_vs_murky() -> None:
    config = SwindleConfig()
    c = _candidate()
    t_w = RevWeights.from_config(config, mode_weight_scale(MODE_TACTICAL))
    m_w = RevWeights.from_config(config, mode_weight_scale(MODE_MURKY))
    t_total, _ = compute_rev_score(c, t_w)
    m_total, _ = compute_rev_score(c, m_w)
    assert t_total != m_total


def test_mate_priority_flag_source() -> None:
    c = CandidateMove(move="8h2b+", pv=["8h2b+"], base_cp=-1500, mate_score=5, depth=18)
    assert c.mate_score is not None and c.mate_score > 0


if __name__ == "__main__":
    test_rev_breakdown_matches_total()
    test_mode_difference_tactical_vs_murky()
    test_mate_priority_flag_source()
    print("ok test_scoring")
