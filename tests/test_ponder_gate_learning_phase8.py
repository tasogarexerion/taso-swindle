from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.config import SwindleConfig
from taso_swindle.info_parser import InfoParseResult
from taso_swindle.swindle.context import SwindleContext
from taso_swindle.swindle.controller import Stage1Decision
from taso_swindle.swindle.weight_tuner import WeightTuner
from taso_swindle.usi_protocol import PonderCacheEntry, USIProtocol


def _run(cmd: list[str], cwd: Path) -> None:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode != 0:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")


def _write_event_jsonl(path: Path) -> None:
    rec = {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": "g1",
        "ply": 10,
        "search_id": 1,
        "ponder_cache_hit": True,
        "ponder_cache_used": True,
        "ponder_status_summary": "ok",
        "ponder_used_budget_ms": 180,
        "ponder_cache_age_ms": 300,
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "candidates": [
            {
                "move": "7g7f",
                "gap12": 420.0,
                "mate": None,
                "reply_topk": [{"move": "3c3d"}, {"move": "8c8d"}, {"move": "4c4d"}],
            }
        ],
    }
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")


def test_train_ponder_gate_outputs_metadata() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-ponder-train-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        out_path = d / "weights.json"
        _write_event_jsonl(in_path)
        _run(
            [
                sys.executable,
                "scripts/train_ponder_gate.py",
                "--input",
                str(in_path),
                "--output",
                str(out_path),
                "--label-mode",
                "heuristic",
            ],
            ROOT,
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["kind"] == "ponder_gate_adjustment"
        assert payload["trained_samples"] >= 1
        assert "features_version" in payload
        assert "threshold_suggested" in payload
        assert isinstance(payload.get("weights"), dict) and payload["weights"]


def test_runtime_ponder_gate_adjustment_capped() -> None:
    tuner = WeightTuner()
    with tempfile.TemporaryDirectory(prefix="taso-s8-ponder-cap-") as td:
        path = Path(td) / "weights.json"
        payload = {
            "kind": "ponder_gate_adjustment",
            "features_version": "v1",
            "weights": {"bias": 10.0},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert tuner.load_ponder_gate_weights(str(path)) is True
        delta, source, used = tuner.get_ponder_gate_adjustment({"candidate_count": 2}, cap_pct=5.0)
        assert used is True
        assert source in {"file", "learned:heuristic", "learned:mixed", "learned:runtime_first"}
        assert abs(delta) <= 0.05 + 1e-9


def test_version_mismatch_noop() -> None:
    tuner = WeightTuner()
    with tempfile.TemporaryDirectory(prefix="taso-s8-ponder-vm-") as td:
        path = Path(td) / "weights.json"
        payload = {
            "kind": "ponder_gate_adjustment",
            "features_version": "mismatch-v9",
            "weights": {"bias": 0.4},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert tuner.load_ponder_gate_weights(str(path)) is True
        delta, source, used = tuner.get_ponder_gate_adjustment(
            {"candidate_count": 4},
            require_feature_version_match=True,
        )
        assert used is False
        assert delta == 0.0
        assert source == "version_mismatch"


def test_learned_adjustment_does_not_force_reuse_when_gate_hard_fail() -> None:
    cfg = SwindleConfig()
    cfg.swindle_ponder_enable = True
    cfg.swindle_use_ponder_gate_learned_adjustment = True
    cfg.swindle_ponder_require_verify_for_mate_cache = True
    cfg.swindle_ponder_reuse_min_score = 0

    proto = USIProtocol(cfg)
    with tempfile.TemporaryDirectory(prefix="taso-s8-ponder-hard-") as td:
        w = Path(td) / "weights.json"
        w.write_text(
            json.dumps(
                {
                    "kind": "ponder_gate_adjustment",
                    "features_version": "v1",
                    "weights": {"bias": 0.9},
                }
            ),
            encoding="utf-8",
        )
        proto.config.swindle_ponder_gate_weights_path = str(w)
        proto._sync_ponder_gate_weights_config()

    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time(),
        candidate_count=6,
        top_gap12=500.0,
        had_mate_signal=True,
        elapsed_ms=220,
        reply_coverage=0.95,
        verify_done=False,
    )
    cached, hit, had, score, _age_ms, reason = proto._consume_ponder_cache("position startpos", max_age_sec=2.0)
    assert cached is None
    assert hit is True
    assert had is True
    assert score is not None
    assert reason == "mate_without_verify"
    assert proto._last_ponder_gate_adjustment_used is False


def test_event_logs_adjustment_fields() -> None:
    cfg = SwindleConfig()
    proto = USIProtocol(cfg)
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
    decision.ponder_gate_learned_adjustment_used = True
    decision.ponder_gate_adjustment_delta = 0.08
    decision.ponder_gate_adjustment_source = "file"

    ctx = SwindleContext(
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
        context=ctx,
        go_time_info={"movetime": 200},
        search_id=1,
        normal_bestmove="7g7f",
        final_bestmove="7g7f",
        selected_reason="rev_max",
        backend_restart_count=0,
    )
    assert captured
    event = captured[-1]
    assert event.ponder_gate_learned_adjustment_used is True
    assert abs(event.ponder_gate_adjustment_delta - 0.08) < 1e-9
    assert event.ponder_gate_adjustment_source in {"file", "learned:heuristic", "learned:mixed", "learned:runtime_first"}


def test_rule_base_still_works_without_weights() -> None:
    cfg = SwindleConfig()
    cfg.swindle_ponder_enable = True
    cfg.swindle_use_ponder_gate_learned_adjustment = False
    cfg.swindle_ponder_reuse_min_score = 20
    cfg.swindle_ponder_require_verify_for_mate_cache = False
    proto = USIProtocol(cfg)
    proto._ponder_cache = PonderCacheEntry(
        position_cmd="position startpos",
        info_result=InfoParseResult(),
        created_ts=time.time(),
        candidate_count=5,
        top_gap12=460.0,
        had_mate_signal=False,
        elapsed_ms=180,
        reply_coverage=0.9,
        verify_done=True,
    )
    cached, hit, had, score, _age_ms, reason = proto._consume_ponder_cache("position startpos", max_age_sec=2.0)
    assert had and hit
    assert cached is not None
    assert score is not None and score >= 0.2
    assert reason is None


if __name__ == "__main__":
    test_train_ponder_gate_outputs_metadata()
    test_runtime_ponder_gate_adjustment_capped()
    test_version_mismatch_noop()
    test_learned_adjustment_does_not_force_reuse_when_gate_hard_fail()
    test_event_logs_adjustment_fields()
    test_rule_base_still_works_without_weights()
    print("ok test_ponder_gate_learning_phase8")
