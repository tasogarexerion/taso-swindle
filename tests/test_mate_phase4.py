from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.info_parser import InfoParseResult, InfoSnapshot
from taso_swindle.mate import MateResult
from taso_swindle.swindle.context import SwindleContext
from taso_swindle.swindle.controller import SwindleController
from taso_swindle.swindle.pseudo_hisshi import PseudoHisshiEstimator
from taso_swindle.swindle.weight_tuner import WeightTuner


def _root_info() -> InfoParseResult:
    result = InfoParseResult()
    result.upsert(InfoSnapshot(multipv=1, depth=16, cp=-420, move="2g2f", pv=["2g2f", "8c8d"]))
    result.upsert(InfoSnapshot(multipv=2, depth=16, cp=-470, move="7g7f", pv=["7g7f", "3c3d"]))
    result.upsert(InfoSnapshot(multipv=3, depth=16, cp=-520, move="3g3f", pv=["3g3f", "4c4d"]))
    result.upsert(InfoSnapshot(multipv=4, depth=16, cp=-560, move="2h7h", pv=["2h7h", "8c8d"]))
    return result


def _context(config: SwindleConfig) -> SwindleContext:
    return SwindleContext(
        side_to_move="b",
        root_sfen="startpos",
        root_position_cmd="position startpos",
        root_eval_cp=-420,
        root_mate_score=None,
        is_losing=True,
        is_lost=False,
        time_left_ms=12000,
        byoyomi_ms=1000,
        increment_ms=0,
        mode=config.swindle_mode,
        swindle_enabled=True,
        emergency_fast_mode=False,
        dynamic_drop_cap_cp=config.dynamic_drop_cap_cp(-420, None),
    )


class _DummyAdapter:
    def __init__(self, fn):
        self._fn = fn
        self.calls: list[tuple[str, int, str]] = []

    def available(self) -> bool:
        return True

    def verify(self, sfen: str, move: str, timeout_ms: int, *, mode: str = "VERIFY_ONLY", root_position_cmd=None):
        _ = (sfen, root_position_cmd)
        self.calls.append((move, timeout_ms, mode))
        return self._fn(move, timeout_ms, mode)


def test_verify_mode_top_candidates_selects_multiple() -> None:
    cfg = SwindleConfig()
    cfg.swindle_use_mate_engine_verification = True
    cfg.swindle_verify_mode = "TOP_CANDIDATES"
    cfg.swindle_verify_max_candidates = 3

    adapter = _DummyAdapter(lambda m, t, md: MateResult(found_mate=False, status="not_used", engine_kind="backend"))
    dec = SwindleController(cfg, PseudoHisshiEstimator(), WeightTuner()).select_stage1(
        _context(cfg),
        _root_info(),
        normal_bestmove="2g2f",
        mate_adapter=adapter,
    )
    assert dec.verify_mode_used == "TOP_CANDIDATES"
    assert dec.mate_verify_candidates_count == 3
    assert len(adapter.calls) == 3


def test_verify_mode_aggressive_uses_extra_budget() -> None:
    cfg = SwindleConfig()
    cfg.swindle_use_mate_engine_verification = True
    cfg.swindle_verify_mode = "AGGRESSIVE"
    cfg.swindle_mate_verify_time_ms = 120
    cfg.swindle_verify_aggressive_extra_ms = 200

    adapter = _DummyAdapter(lambda m, t, md: MateResult(found_mate=False, status="not_used", engine_kind="backend"))
    SwindleController(cfg, PseudoHisshiEstimator(), WeightTuner()).select_stage1(
        _context(cfg),
        _root_info(),
        normal_bestmove="2g2f",
        mate_adapter=adapter,
    )
    assert adapter.calls
    assert all(timeout_ms >= 320 for _, timeout_ms, _ in adapter.calls)
    assert all(mode == "AGGRESSIVE" for _, _, mode in adapter.calls)


def test_dfpn_timeout_is_event_only() -> None:
    cfg = SwindleConfig()
    cfg.swindle_use_mate_engine_verification = True
    cfg.swindle_verify_mode = "VERIFY_ONLY"

    def _fn(move: str, timeout_ms: int, mode: str) -> MateResult:
        _ = (move, timeout_ms, mode)
        return MateResult(
            found_mate=False,
            status="not_used",
            engine_kind="hybrid",
            notes=["dfpn_status:timeout", "dfpn_timeout"],
        )

    adapter = _DummyAdapter(_fn)
    dec = SwindleController(cfg, PseudoHisshiEstimator(), WeightTuner()).select_stage1(
        _context(cfg),
        _root_info(),
        normal_bestmove="2g2f",
        mate_adapter=adapter,
    )
    assert any(evt == "dfpn_timeout" for evt in dec.events)
    assert all("search_notes" not in c.__dict__ for c in dec.candidates)


def test_dfpn_error_does_not_break_bestmove() -> None:
    cfg = SwindleConfig()
    cfg.swindle_use_mate_engine_verification = True

    def _fn(move: str, timeout_ms: int, mode: str) -> MateResult:
        _ = (move, timeout_ms, mode)
        return MateResult(
            found_mate=False,
            status="not_used",
            engine_kind="hybrid",
            notes=["dfpn_status:error", "dfpn_error"],
        )

    adapter = _DummyAdapter(_fn)
    dec = SwindleController(cfg, PseudoHisshiEstimator(), WeightTuner()).select_stage1(
        _context(cfg),
        _root_info(),
        normal_bestmove="2g2f",
        mate_adapter=adapter,
    )
    assert dec.selected_move
    assert dec.selected_move in {"2g2f", "7g7f", "3g3f", "2h7h"}
    assert any(evt == "dfpn_error" for evt in dec.events)


def test_verify_conflict_conservative_resolution() -> None:
    cfg = SwindleConfig()
    cfg.swindle_use_mate_engine_verification = True
    cfg.swindle_verify_mode = "VERIFY_ONLY"

    def _fn(move: str, timeout_ms: int, mode: str) -> MateResult:
        _ = (timeout_ms, mode)
        if move == "2g2f":
            return MateResult(found_mate=False, status="rejected", engine_kind="hybrid", mate_sign="for_them")
        return MateResult(found_mate=False, status="not_used", engine_kind="backend")

    adapter = _DummyAdapter(_fn)
    dec = SwindleController(cfg, PseudoHisshiEstimator(), WeightTuner()).select_stage1(
        _context(cfg),
        _root_info(),
        normal_bestmove="2g2f",
        mate_adapter=adapter,
    )
    assert dec.selected_move != "2g2f"
    assert dec.verify_status_summary in {"rejected", "not_used", "confirmed"}


if __name__ == "__main__":
    test_verify_mode_top_candidates_selects_multiple()
    test_verify_mode_aggressive_uses_extra_budget()
    test_dfpn_timeout_is_event_only()
    test_dfpn_error_does_not_break_bestmove()
    test_verify_conflict_conservative_resolution()
    print("ok test_mate_phase4")
