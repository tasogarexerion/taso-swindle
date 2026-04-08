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
from taso_swindle.usi_protocol import USIProtocol
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
    def __init__(self, sleep_sec: float = 0.0) -> None:
        self.sleep_sec = sleep_sec
        self.last_mate_adapter = object()
        self.last_use_dfpn: bool | None = None

    def select_stage1(self, context, info_result, normal_bestmove, **kwargs):  # type: ignore[no-untyped-def]
        _ = (context, info_result, normal_bestmove)
        mate_adapter = kwargs.get("mate_adapter")
        self.last_mate_adapter = mate_adapter
        if mate_adapter is not None:
            self.last_use_dfpn = bool(getattr(mate_adapter, "use_dfpn", False))
        if self.sleep_sec > 0:
            time.sleep(self.sleep_sec)
        return Stage1Decision(
            normal_bestmove=normal_bestmove,
            selected_move=normal_bestmove,
            selected_reason="rev_max",
            candidates=[],
            activated=True,
            mode="HYBRID",
        )


def _root_info() -> InfoParseResult:
    result = InfoParseResult()
    result.upsert(InfoSnapshot(multipv=1, depth=12, cp=-900, move="7g7f", pv=["7g7f", "3c3d"]))
    return result


def _build_protocol() -> tuple[USIProtocol, list[str]]:
    cfg = SwindleConfig()
    cfg.swindle_enable = True
    cfg.swindle_dry_run = False
    cfg.swindle_ponder_enable = True
    cfg.swindle_ponder_reuse_min_score = 0
    cfg.swindle_ponder_require_verify_for_mate_cache = False
    cfg.swindle_use_mate_engine_verification = True
    proto = USIProtocol(cfg)
    proto.position_state.raw_position = "position startpos"
    proto.position_state.root_sfen = "startpos"
    proto._ensure_engine_started = lambda: True  # type: ignore[assignment]
    proto._initialize_backend_usi = lambda: True  # type: ignore[assignment]
    proto._apply_backend_options = lambda: None  # type: ignore[assignment]
    proto.engine = _DummyEngine()  # type: ignore[assignment]
    outputs: list[str] = []
    proto._out = lambda text: outputs.append(text)  # type: ignore[assignment]
    proto.logger.log_decision = lambda event: outputs.append(f"log:{event.ponder_status_summary}:{event.ponder_used_budget_ms}")  # type: ignore[assignment]
    return proto, outputs


def test_ponder_cache_hit_reuses_snapshot() -> None:
    proto, _ = _build_protocol()
    src = _root_info()
    proto._store_ponder_cache("position startpos", src)
    cached, hit, had, _, _, _ = proto._consume_ponder_cache("position startpos", max_age_sec=30.0)
    assert had is True
    assert hit is True
    assert cached is not None
    assert "7g7f" in cached.by_move


def test_ponder_cache_miss_discards_snapshot() -> None:
    proto, _ = _build_protocol()
    proto._store_ponder_cache("position startpos", _root_info())
    cached, hit, had, _, _, _ = proto._consume_ponder_cache("position sfen x", max_age_sec=30.0)
    assert had is True
    assert hit is False
    assert cached is None


def test_ponder_verify_off_restores_state() -> None:
    proto, _ = _build_protocol()
    proto.config.swindle_ponder_verify = False
    ctrl = _DummyController()
    proto.controller = ctrl  # type: ignore[assignment]
    proto.engine_session = _DummySession(GoOutcome(search_id=1, info_result=_root_info(), backend_bestmove="7g7f"))  # type: ignore[assignment]
    proto._handle_go("go ponder")
    assert ctrl.last_mate_adapter is None


def test_ponder_dfpn_off_restores_state() -> None:
    proto, _ = _build_protocol()
    proto.config.swindle_ponder_verify = True
    proto.config.swindle_ponder_dfpn = False
    proto.config.swindle_use_dfpn = True
    proto.mate_adapter.use_dfpn = True
    ctrl = _DummyController()
    proto.controller = ctrl  # type: ignore[assignment]
    proto.engine_session = _DummySession(GoOutcome(search_id=1, info_result=_root_info(), backend_bestmove="7g7f"))  # type: ignore[assignment]
    proto._handle_go("go ponder")
    assert ctrl.last_use_dfpn is False
    assert proto.mate_adapter.use_dfpn is True


def test_ponder_budget_recorded_event_level() -> None:
    proto, outputs = _build_protocol()
    proto.config.swindle_ponder_verify = True
    proto.config.swindle_ponder_dfpn = True
    proto.config.swindle_ponder_max_ms = 500
    ctrl = _DummyController(sleep_sec=0.02)
    proto.controller = ctrl  # type: ignore[assignment]
    proto.engine_session = _DummySession(GoOutcome(search_id=1, info_result=_root_info(), backend_bestmove="7g7f"))  # type: ignore[assignment]
    proto._handle_go("go ponder")
    assert any(line.startswith("log:") for line in outputs)
    budget_tokens = [line for line in outputs if line.startswith("log:")]
    assert budget_tokens
    # log format: log:<ponder_status>:<ms>
    last = budget_tokens[-1].split(":")
    assert int(last[-1]) >= 0


if __name__ == "__main__":
    test_ponder_cache_hit_reuses_snapshot()
    test_ponder_cache_miss_discards_snapshot()
    test_ponder_verify_off_restores_state()
    test_ponder_dfpn_off_restores_state()
    test_ponder_budget_recorded_event_level()
    print("ok test_ponder_quality_phase6")
