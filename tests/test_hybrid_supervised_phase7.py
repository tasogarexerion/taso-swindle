from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.mate_adapter import MateAdapter
from taso_swindle.mate.mate_result import MateResult
from taso_swindle.swindle.weight_tuner import HYBRID_FEATURES_VERSION, WeightTuner


def _decision_event(
    *,
    actual_move: str | None = "7c7d",
    outcome_tag: str | None = "win",
    actual_in_topk: bool | None = None,
    actual_rank: int | None = None,
) -> dict:
    return {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": "g1",
        "ply": 10,
        "search_id": 77,
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "confirmed",
        "verify_mode_used": "TOP_CANDIDATES",
        "dfpn_parser_mode": "AUTO",
        "dfpn_parser_hits": ["generic_en:strict:mate_for_us"],
        "verify_conflict_count": 0,
        "verify_unknown_count": 0,
        "emergency_fast_mode": False,
        "final_bestmove": "7g7f",
        "actual_opponent_move": actual_move,
        "actual_move_in_reply_topk": actual_in_topk,
        "actual_move_rank_in_reply_topk": actual_rank,
        "outcome_tag": outcome_tag,
        "outcome_confidence": 0.8 if outcome_tag else None,
        "candidates": [
            {
                "move": "7g7f",
                "reply_topk": [
                    {"move": "7c7d"},
                    {"move": "8c8d"},
                    {"move": "3c3d"},
                ],
            }
        ],
    }


def _run(cmd: list[str], cwd: Path) -> None:
    completed = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if completed.returncode != 0:
        raise AssertionError(f"command failed rc={completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")


def test_build_training_labels_matches_actual_move_rank() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-label-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        out_path = d / "labels.jsonl"
        rec = _decision_event(actual_move="8c8d", outcome_tag="win", actual_in_topk=None, actual_rank=None)
        in_path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/build_training_labels.py",
                "--input",
                str(in_path),
                "--output",
                str(out_path),
                "--label-mode",
                "supervised",
            ],
            ROOT,
        )

        lines = [x for x in out_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert lines
        out = json.loads(lines[0])
        assert out["actual_move_in_reply_topk"] is True
        assert out["actual_move_rank_in_reply_topk"] == 2


def test_train_supervised_outputs_metadata() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-train-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        out_path = d / "weights.json"
        in_path.write_text(json.dumps(_decision_event(outcome_tag="win"), ensure_ascii=False) + "\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/train_hybrid_confidence.py",
                "--input",
                str(in_path),
                "--output",
                str(out_path),
                "--label-mode",
                "supervised",
            ],
            ROOT,
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["label_mode"] == "supervised"
        assert payload["trained_samples"] >= 1
        assert payload["features_version"] == HYBRID_FEATURES_VERSION


def test_train_mixed_falls_back_to_pseudo() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-train-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        out_path = d / "weights.json"
        rec = _decision_event(outcome_tag=None, actual_move=None, actual_in_topk=None, actual_rank=None)
        rec["selected_reason"] = "rev_max"
        in_path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/train_hybrid_confidence.py",
                "--input",
                str(in_path),
                "--output",
                str(out_path),
                "--label-mode",
                "mixed",
            ],
            ROOT,
        )
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["label_mode"] == "mixed"
        assert payload["trained_samples"] >= 1
        assert payload["pseudo_samples"] >= 1


def test_weight_loader_feature_version_mismatch_noop() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-weight-") as td:
        path = Path(td) / "w.json"
        payload = {
            "version": 2,
            "kind": "hybrid_adjustment",
            "source": "test",
            "features_version": "vX",
            "weights": {"bias": 0.5},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        tuner = WeightTuner()
        assert tuner.load_hybrid_weights(str(path))
        delta, source, used = tuner.get_hybrid_adjustment(
            {"verifier_sign": "for_us"},
            cap_pct=20,
            require_feature_version_match=True,
            runtime_features_version=HYBRID_FEATURES_VERSION,
        )
        assert used is False
        assert delta == 0.0
        assert source == "version_mismatch"


def test_runtime_adjustment_supervised_stays_capped() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-weight-") as td:
        path = Path(td) / "w.json"
        payload = {
            "version": 2,
            "kind": "hybrid_adjustment",
            "source": "test",
            "features_version": HYBRID_FEATURES_VERSION,
            "weights": {"bias": 9.0},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        tuner = WeightTuner()
        assert tuner.load_hybrid_weights(str(path))
        delta, source, used = tuner.get_hybrid_adjustment(
            {"verifier_sign": "for_us"},
            cap_pct=5,
            require_feature_version_match=True,
            runtime_features_version=HYBRID_FEATURES_VERSION,
        )
        assert used is True
        assert source == "file"
        assert -0.05 <= delta <= 0.05


def test_mate_sign_not_flipped_by_learning() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-weight-") as td:
        path = Path(td) / "w.json"
        payload = {
            "version": 2,
            "kind": "hybrid_adjustment",
            "source": "test",
            "features_version": HYBRID_FEATURES_VERSION,
            "weights": {"bias": 1.0, "agree": 0.5, "conflict": -0.2},
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        adapter = MateAdapter("")
        adapter.configure_runtime(
            use_hybrid_learned_adjustment=True,
            hybrid_weights_path=str(path),
            hybrid_adjustment_cap_pct=10,
            hybrid_require_feature_version_match=True,
        )
        merged = MateResult(
            found_mate=False,
            status="rejected",
            mate_sign="for_them",
            confidence=0.82,
            engine_kind="hybrid",
        )
        verifier = MateResult(found_mate=False, status="rejected", mate_sign="for_them", confidence=0.82)
        dfpn = MateResult(
            found_mate=False,
            status="rejected",
            mate_sign="for_them",
            confidence=0.78,
            source_detail="dfpn:generic_en:strict:mate_for_them",
        )
        out = adapter._apply_learned_adjustment(merged, verifier_snapshot=verifier, dfpn_result=dfpn)
        assert out.mate_sign in {"for_them", "unknown"}
        assert out.mate_sign != "for_us"


if __name__ == "__main__":
    test_build_training_labels_matches_actual_move_rank()
    test_train_supervised_outputs_metadata()
    test_train_mixed_falls_back_to_pseudo()
    test_weight_loader_feature_version_mismatch_noop()
    test_runtime_adjustment_supervised_stays_capped()
    test_mate_sign_not_flipped_by_learning()
    print("ok test_hybrid_supervised_phase7")
