from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.mate_adapter import MateAdapter
from taso_swindle.mate.mate_result import MateResult
from taso_swindle.swindle.weight_tuner import WeightTuner


def _weights_payload() -> dict:
    return {
        "version": 1,
        "kind": "hybrid_adjustment",
        "source": "test",
        "weights": {
            "bias": 0.5,
            "agree": 0.3,
            "conflict": -0.8,
            "verifier_conf": 0.4,
            "dfpn_conf": 0.4,
        },
    }


def test_weight_loader_missing_file_noop() -> None:
    tuner = WeightTuner()
    ok = tuner.load_hybrid_weights("/tmp/not-found-hybrid-weights.json")
    assert ok is False
    delta, source, used = tuner.get_hybrid_adjustment({}, cap_pct=10)
    assert used is False
    assert delta == 0.0
    assert source in {"missing", "none"}


def test_weight_loader_valid_file() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s6-weight-") as td:
        path = Path(td) / "w.json"
        path.write_text(json.dumps(_weights_payload()), encoding="utf-8")
        tuner = WeightTuner()
        ok = tuner.load_hybrid_weights(str(path))
        assert ok is True
        delta, source, used = tuner.get_hybrid_adjustment(
            {
                "verifier_sign": "for_us",
                "dfpn_sign": "for_us",
                "verifier_confidence": 0.7,
                "dfpn_confidence": 0.6,
            },
            cap_pct=15,
        )
        assert used is True
        assert source == "file"
        assert isinstance(delta, float)


def test_hybrid_adjustment_clamped() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s6-weight-") as td:
        path = Path(td) / "w.json"
        payload = _weights_payload()
        payload["weights"]["bias"] = 10.0
        path.write_text(json.dumps(payload), encoding="utf-8")
        tuner = WeightTuner()
        assert tuner.load_hybrid_weights(str(path))
        delta, _, used = tuner.get_hybrid_adjustment({"verifier_sign": "for_us"}, cap_pct=5)
        assert used is True
        assert -0.05 <= delta <= 0.05


def test_mate_adapter_hybrid_with_learned_adjustment_safe_cap() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s6-weight-") as td:
        path = Path(td) / "w.json"
        payload = _weights_payload()
        payload["weights"]["bias"] = 8.0
        path.write_text(json.dumps(payload), encoding="utf-8")

        adapter = MateAdapter("")
        adapter.configure_runtime(
            use_hybrid_learned_adjustment=True,
            hybrid_weights_path=str(path),
            hybrid_adjustment_cap_pct=10,
        )
        merged = MateResult(
            found_mate=False,
            status="rejected",
            mate_sign="for_them",
            confidence=0.53,
            engine_kind="hybrid",
        )
        verifier = MateResult(found_mate=False, status="rejected", mate_sign="for_them", confidence=0.53)
        dfpn = MateResult(found_mate=False, status="rejected", mate_sign="for_them", confidence=0.60, source_detail="dfpn:strict:mate_for_them")
        out = adapter._apply_learned_adjustment(merged, verifier_snapshot=verifier, dfpn_result=dfpn)
        assert out.hybrid_learned_adjustment_used is True
        assert -0.10 <= out.hybrid_adjustment_delta <= 0.10


def test_learning_disabled_keeps_rule_based() -> None:
    adapter = MateAdapter("")
    adapter.configure_runtime(use_hybrid_learned_adjustment=False)
    merged = MateResult(found_mate=False, status="rejected", mate_sign="for_them", confidence=0.6, engine_kind="hybrid")
    verifier = MateResult(found_mate=False, status="rejected", mate_sign="for_them", confidence=0.6)
    dfpn = MateResult(found_mate=False, status="rejected", mate_sign="for_them", confidence=0.6)
    out = adapter._apply_learned_adjustment(merged, verifier_snapshot=verifier, dfpn_result=dfpn)
    assert out.hybrid_learned_adjustment_used is False
    assert out.hybrid_adjustment_delta == 0.0
    assert out.status == "rejected"


if __name__ == "__main__":
    test_weight_loader_missing_file_noop()
    test_weight_loader_valid_file()
    test_hybrid_adjustment_clamped()
    test_mate_adapter_hybrid_with_learned_adjustment_safe_cap()
    test_learning_disabled_keeps_rule_based()
    print("ok test_hybrid_learning_phase6")
