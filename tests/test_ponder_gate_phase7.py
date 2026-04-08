from __future__ import annotations

import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.info_parser import InfoParseResult
from taso_swindle.swindle.context import SwindleContext
from taso_swindle.swindle.controller import Stage1Decision
from taso_swindle.usi_protocol import PonderCacheEntry, USIProtocol


def _proto() -> USIProtocol:
    cfg = SwindleConfig()
    cfg.swindle_ponder_enable = True
    cfg.swindle_ponder_reuse_min_score = 55
    cfg.swindle_ponder_cache_max_age_ms = 3000
    cfg.swindle_ponder_require_verify_for_mate_cache = True
    return USIProtocol(cfg)


def test_reuse_score_high_with_good_coverage() -> None:
    proto = _proto()
    entry = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time(),
        candidate_count=6,
        top_gap12=480.0,
        had_mate_signal=False,
        elapsed_ms=220,
        reply_coverage=0.95,
        verify_done=True,
    )
    score, reason = proto._ponder_reuse_score(entry=entry, age_ms=200)
    assert score >= 0.55
    assert reason is None


def test_reuse_score_low_with_stale_cache() -> None:
    proto = _proto()
    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time() - 10.0,
        candidate_count=5,
        top_gap12=320.0,
        had_mate_signal=False,
        elapsed_ms=200,
        reply_coverage=0.8,
        verify_done=True,
    )
    cached, hit, had, score, age_ms, reason = proto._consume_ponder_cache("position startpos", max_age_sec=0.5)
    assert cached is None
    assert hit is False
    assert had is True
    assert score == 0.0
    assert age_ms >= 500
    assert reason == "stale"


def test_gate_blocks_low_quality_cache() -> None:
    proto = _proto()
    proto.config.swindle_ponder_reuse_min_score = 90
    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time(),
        candidate_count=1,
        top_gap12=0.0,
        had_mate_signal=False,
        elapsed_ms=5,
        reply_coverage=0.0,
        verify_done=False,
    )
    cached, hit, had, score, _age_ms, reason = proto._consume_ponder_cache("position startpos", max_age_sec=2.0)
    assert cached is None
    assert hit is True
    assert had is True
    assert score is not None and score < 0.90
    assert reason in {"quality_gate", "mate_without_verify"}


def test_gate_blocks_mate_signal_without_verify_when_required() -> None:
    proto = _proto()
    proto.config.swindle_ponder_reuse_min_score = 0
    proto.config.swindle_ponder_require_verify_for_mate_cache = True
    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time(),
        candidate_count=6,
        top_gap12=520.0,
        had_mate_signal=True,
        elapsed_ms=260,
        reply_coverage=1.0,
        verify_done=False,
    )
    cached, hit, had, score, _age_ms, reason = proto._consume_ponder_cache("position startpos", max_age_sec=2.0)
    assert cached is None
    assert hit is True
    assert had is True
    assert score is not None
    assert reason == "mate_without_verify"


def test_gate_accepts_good_cache() -> None:
    proto = _proto()
    proto.config.swindle_ponder_reuse_min_score = 40
    proto.config.swindle_ponder_require_verify_for_mate_cache = False
    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time(),
        candidate_count=5,
        top_gap12=420.0,
        had_mate_signal=False,
        elapsed_ms=180,
        reply_coverage=0.9,
        verify_done=True,
    )
    cached, hit, had, score, _age_ms, reason = proto._consume_ponder_cache("position startpos", max_age_sec=2.0)
    assert cached is not None
    assert hit is True
    assert had is True
    assert score is not None and score >= 0.4
    assert reason is None


def test_event_level_gate_reason_recorded() -> None:
    proto = _proto()
    captured = []
    proto.logger.log_decision = lambda event: captured.append(event)  # type: ignore[assignment]

    decision = Stage1Decision(
        normal_bestmove="7g7f",
        selected_move="7g7f",
        selected_reason="rev_max",
        candidates=[],
        activated=True,
        mode="HYBRID",
    )
    decision.ponder_cache_hit = True
    decision.ponder_cache_used = False
    decision.ponder_reuse_score = 0.22
    decision.ponder_cache_age_ms = 1800
    decision.ponder_cache_gate_reason = "quality_gate"

    context = SwindleContext(
        side_to_move="b",
        root_sfen="startpos",
        root_position_cmd="position startpos",
        root_eval_cp=-400,
        root_mate_score=None,
        is_losing=True,
        is_lost=False,
        time_left_ms=5000,
        byoyomi_ms=1000,
        increment_ms=0,
        mode="HYBRID",
        swindle_enabled=True,
        emergency_fast_mode=False,
        dynamic_drop_cap_cp=600,
    )

    proto._emit_log(
        decision=decision,
        context=context,
        go_time_info={"movetime": 200},
        search_id=1,
        normal_bestmove="7g7f",
        final_bestmove="7g7f",
        selected_reason="rev_max",
        backend_restart_count=0,
    )

    assert captured
    event = captured[-1]
    assert event.ponder_cache_gate_reason == "quality_gate"
    assert event.ponder_reuse_score == 0.22


if __name__ == "__main__":
    test_reuse_score_high_with_good_coverage()
    test_reuse_score_low_with_stale_cache()
    test_gate_blocks_low_quality_cache()
    test_gate_blocks_mate_signal_without_verify_when_required()
    test_gate_accepts_good_cache()
    test_event_level_gate_reason_recorded()
    print("ok test_ponder_gate_phase7")
