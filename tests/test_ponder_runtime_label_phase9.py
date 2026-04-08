from __future__ import annotations

import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.engine_session import GoOutcome
from taso_swindle.info_parser import InfoParseResult, InfoSnapshot
from taso_swindle.usi_protocol import PonderCacheEntry, USIProtocol
from taso_swindle.swindle.controller import Stage1Decision


class _DummyEngine:
    alive = True

    def send(self, line: str) -> None:
        _ = line


class _DummySession:
    def __init__(self, outcome: GoOutcome) -> None:
        self._outcome = outcome

    def run_go(self, go_line, stdin_reader, forward_engine_info=False, on_engine_line=None):  # type: ignore[no-untyped-def]
        _ = (go_line, stdin_reader, forward_engine_info, on_engine_line)
        return self._outcome


class _DummyController:
    def __init__(self, selected_move: str) -> None:
        self.selected_move = selected_move

    def select_stage1(self, context, info_result, normal_bestmove, **kwargs):  # type: ignore[no-untyped-def]
        _ = (context, info_result, normal_bestmove, kwargs)
        return Stage1Decision(
            normal_bestmove=normal_bestmove,
            selected_move=self.selected_move,
            selected_reason="rev_max",
            candidates=[],
            activated=True,
            mode="HYBRID",
        )


def _root_info(cp: int = -900) -> InfoParseResult:
    result = InfoParseResult()
    result.upsert(InfoSnapshot(multipv=1, depth=12, cp=cp, move="2g2f", pv=["2g2f", "8c8d"]))
    return result


def _build_proto(selected_move: str, *, predicted_move: str | None) -> tuple[USIProtocol, list]:
    cfg = SwindleConfig()
    cfg.swindle_enable = True
    cfg.swindle_dry_run = False
    cfg.swindle_ponder_enable = True
    cfg.swindle_ponder_reuse_min_score = 0
    cfg.swindle_ponder_require_verify_for_mate_cache = False
    proto = USIProtocol(cfg)
    proto.position_state.raw_position = "position startpos"
    proto.position_state.root_sfen = "startpos"

    proto._ensure_engine_started = lambda: True  # type: ignore[assignment]
    proto._initialize_backend_usi = lambda: True  # type: ignore[assignment]
    proto._apply_backend_options = lambda: None  # type: ignore[assignment]
    proto.engine = _DummyEngine()  # type: ignore[assignment]
    proto.engine_session = _DummySession(
        GoOutcome(
            search_id=1,
            info_result=_root_info(),
            backend_bestmove="2g2f",
        )
    )  # type: ignore[assignment]
    proto.controller = _DummyController(selected_move)  # type: ignore[assignment]

    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=_root_info(-850),
        created_ts=time.time(),
        candidate_count=4,
        top_gap12=380.0,
        had_mate_signal=False,
        elapsed_ms=160,
        reply_coverage=0.9,
        verify_done=True,
        predicted_move=predicted_move,
        decision_id="ponder:test:1",
        parent_position_key="position startpos",
    )

    captured = []
    proto.logger.log_decision = lambda event: captured.append(event)  # type: ignore[assignment]
    return proto, captured


def test_ponder_runtime_label_logged_on_cache_hit() -> None:
    proto, captured = _build_proto("7g7f", predicted_move="2g2f")
    proto._handle_go("go movetime 120")
    assert captured
    event = captured[-1]
    assert event.ponder_cache_used is True
    assert event.reuse_then_bestmove_changed is True
    assert event.ponder_label_source == "runtime_observed"
    assert event.ponder_label_confidence >= 0.6
    assert event.ponder_reuse_decision_id == "ponder:test:1"
    assert event.ponder_reuse_parent_position_key == "position startpos"


def test_ponder_runtime_label_false_when_same_move() -> None:
    proto, captured = _build_proto("7g7f", predicted_move="7g7f")
    proto._handle_go("go movetime 120")
    assert captured
    event = captured[-1]
    assert event.ponder_cache_used is True
    assert event.reuse_then_bestmove_changed is False
    assert event.ponder_label_source == "runtime_observed"


def test_ponder_runtime_label_fallback_to_heuristic_when_missing() -> None:
    proto, captured = _build_proto("7g7f", predicted_move=None)
    proto._handle_go("go movetime 120")
    assert captured
    event = captured[-1]
    assert event.ponder_cache_used is True
    assert event.reuse_then_bestmove_changed is False
    assert event.ponder_label_source == "heuristic"
    assert 0.0 <= event.ponder_label_confidence <= 0.5


if __name__ == "__main__":
    test_ponder_runtime_label_logged_on_cache_hit()
    test_ponder_runtime_label_false_when_same_move()
    test_ponder_runtime_label_fallback_to_heuristic_when_missing()
    print("ok test_ponder_runtime_label_phase9")
