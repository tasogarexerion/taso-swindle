import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.info_parser import InfoParseResult, InfoSnapshot
from taso_swindle.logging import DecisionEvent, JsonlLogger
from taso_swindle.mate import MateAdapter
from taso_swindle.swindle.context import SwindleContext
from taso_swindle.swindle.controller import SwindleController
from taso_swindle.swindle.pseudo_hisshi import PseudoHisshiEstimator
from taso_swindle.swindle.reply_search import ProbeOutcome
from taso_swindle.swindle.weight_tuner import WeightTuner


def _root_info() -> InfoParseResult:
    result = InfoParseResult()
    result.upsert(
        InfoSnapshot(
            multipv=1,
            depth=16,
            cp=-520,
            move="2g2f",
            pv=["2g2f", "8c8d"],
        )
    )
    result.upsert(
        InfoSnapshot(
            multipv=2,
            depth=16,
            cp=-600,
            move="7g7f",
            pv=["7g7f", "3c3d"],
        )
    )
    return result


def _probe_for_move(move: str) -> InfoParseResult:
    result = InfoParseResult()
    if move == "7g7f":
        # High only-move pressure, lower immediate risk.
        result.upsert(InfoSnapshot(multipv=1, depth=9, cp=300, move="R*2b", pv=["R*2b"]))
        result.upsert(InfoSnapshot(multipv=2, depth=9, cp=-200, move="7c7d", pv=["7c7d"]))
        result.upsert(InfoSnapshot(multipv=3, depth=9, cp=-400, move="8c8d", pv=["8c8d"]))
    else:
        # Lower pressure, riskier for root side.
        result.upsert(InfoSnapshot(multipv=1, depth=9, cp=1000, move="7c7d", pv=["7c7d"]))
        result.upsert(InfoSnapshot(multipv=2, depth=9, cp=900, move="8c8d", pv=["8c8d"]))
        result.upsert(InfoSnapshot(multipv=3, depth=9, cp=850, move="3c3d", pv=["3c3d"]))
    return result


def _context(config: SwindleConfig) -> SwindleContext:
    return SwindleContext(
        side_to_move="b",
        root_sfen="startpos",
        root_position_cmd="position startpos",
        root_eval_cp=-520,
        root_mate_score=None,
        is_losing=True,
        is_lost=False,
        time_left_ms=10000,
        byoyomi_ms=1000,
        increment_ms=0,
        mode=config.swindle_mode,
        swindle_enabled=True,
        emergency_fast_mode=False,
        dynamic_drop_cap_cp=config.dynamic_drop_cap_cp(-520, None),
    )


def test_controller_selects_rev_top_and_restores_multipv() -> None:
    config = SwindleConfig()
    config.swindle_mode = "HYBRID"
    config.swindle_reply_multipv = 4
    config.swindle_reply_topk = 3

    calls: list[tuple[str, str]] = []

    def set_backend_option(name: str, value: str) -> None:
        calls.append((name, value))

    def run_probe(position_cmd: str, go_cmd: str) -> ProbeOutcome:
        _ = go_cmd
        move = position_cmd.split()[-1]
        return ProbeOutcome(info_result=_probe_for_move(move), timed_out=False, quit_requested=False)

    controller = SwindleController(config, PseudoHisshiEstimator(), WeightTuner())
    decision = controller.select_stage1(
        _context(config),
        _root_info(),
        normal_bestmove="2g2f",
        run_probe=run_probe,
        set_backend_option=set_backend_option,
        original_multipv=config.swindle_multipv,
        mate_adapter=MateAdapter(""),
    )

    assert decision.selected_move == "7g7f"
    assert decision.selected_reason in {"rev_max", "mate_priority"}
    restore_calls = [c for c in calls if c == ("MultiPV", str(config.swindle_multipv))]
    assert restore_calls


def test_dryrun_keeps_backend_move() -> None:
    config = SwindleConfig()
    config.swindle_dry_run = True
    config.swindle_reply_multipv = 4

    def set_backend_option(name: str, value: str) -> None:
        _ = (name, value)

    def run_probe(position_cmd: str, go_cmd: str) -> ProbeOutcome:
        _ = go_cmd
        move = position_cmd.split()[-1]
        return ProbeOutcome(info_result=_probe_for_move(move), timed_out=False, quit_requested=False)

    controller = SwindleController(config, PseudoHisshiEstimator(), WeightTuner())
    normal = "2g2f"
    decision = controller.select_stage1(
        _context(config),
        _root_info(),
        normal_bestmove=normal,
        run_probe=run_probe,
        set_backend_option=set_backend_option,
        original_multipv=config.swindle_multipv,
        mate_adapter=MateAdapter(""),
    )

    final = normal if config.swindle_dry_run else decision.selected_move
    assert final == normal


def test_restore_failed_is_event_level_and_logged() -> None:
    config = SwindleConfig()
    config.swindle_mode = "HYBRID"
    config.swindle_reply_multipv = 4
    config.swindle_reply_topk = 3
    config.swindle_log_enable = True
    config.swindle_log_format = "JSONL"
    with tempfile.TemporaryDirectory(prefix="taso-swindle-test-") as tmp:
        config.swindle_log_path = tmp

        def set_backend_option(name: str, value: str) -> None:
            # Probe set is allowed; restore set is forced to fail.
            if name == "MultiPV" and value == str(config.swindle_multipv):
                raise RuntimeError("forced restore failure")

        def run_probe(position_cmd: str, go_cmd: str) -> ProbeOutcome:
            _ = go_cmd
            move = position_cmd.split()[-1]
            return ProbeOutcome(info_result=_probe_for_move(move), timed_out=False, quit_requested=False)

        controller = SwindleController(config, PseudoHisshiEstimator(), WeightTuner())
        decision = controller.select_stage1(
            _context(config),
            _root_info(),
            normal_bestmove="2g2f",
            run_probe=run_probe,
            set_backend_option=set_backend_option,
            original_multipv=config.swindle_multipv,
            mate_adapter=MateAdapter(""),
        )

        assert "restore_failed:MultiPV" in decision.events
        assert decision.option_restore_failed
        assert all("search_notes" not in c.__dict__ for c in decision.candidates)

        logger = JsonlLogger(config)
        logger.log_decision(
            DecisionEvent(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                game_id="g",
                ply=1,
                root_sfen="startpos",
                root_eval_cp=-520,
                root_mate=None,
                swindle_enabled=True,
                mode=decision.mode,
                time_info={},
                normal_bestmove="2g2f",
                final_bestmove=decision.selected_move,
                candidates=[],
                selected_reason=decision.selected_reason,
                backend_engine_info={},
                emergency_fast_mode=False,
                events=list(decision.events),
                option_restore_failed=decision.option_restore_failed,
            )
        )

        files = list(Path(tmp).glob("taso-swindle-*.jsonl"))
        assert files
        record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
        assert "restore_failed:MultiPV" in record.get("events", [])
        assert record.get("option_restore_failed") is True


if __name__ == "__main__":
    test_controller_selects_rev_top_and_restores_multipv()
    test_dryrun_keeps_backend_move()
    test_restore_failed_is_event_level_and_logged()
    print("ok test_integration_mock_engine")
