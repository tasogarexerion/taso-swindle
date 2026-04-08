from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.mate.mate_adapter import (
    HYBRID_BALANCED,
    HYBRID_CONSERVATIVE,
    HYBRID_DFPN_FIRST,
    HYBRID_MATE_ENGINE_FIRST,
    MateAdapter,
)
from taso_swindle.mate.mate_result import MateResult


def _base_result() -> MateResult:
    return MateResult(
        found_mate=True,
        status="confirmed",
        mate_sign="for_us",
        confidence=0.78,
        engine_kind="mate_engine",
        notes=[],
    )


def _dfpn_for_them(conf: float = 0.75) -> MateResult:
    return MateResult(
        found_mate=False,
        status="rejected",
        mate_sign="for_them",
        confidence=conf,
        engine_kind="dfpn",
        distance=5,
        notes=["dfpn_status:rejected"],
    )


def _dfpn_for_us(conf: float = 0.72) -> MateResult:
    return MateResult(
        found_mate=True,
        status="confirmed",
        mate_sign="for_us",
        confidence=conf,
        engine_kind="dfpn",
        distance=7,
        notes=["dfpn_status:confirmed"],
    )


def test_hybrid_agree_for_us_high_confidence() -> None:
    adapter = MateAdapter("")
    adapter.verify_hybrid_policy = HYBRID_BALANCED
    merged = adapter._merge_hybrid(_base_result(), _dfpn_for_us(0.81))
    assert merged.status == "confirmed"
    assert merged.mate_sign == "for_us"
    assert merged.confidence >= 0.78


def test_hybrid_conflict_conservative_returns_unknown_or_rejected() -> None:
    adapter = MateAdapter("")
    adapter.verify_hybrid_policy = HYBRID_CONSERVATIVE
    merged = adapter._merge_hybrid(_base_result(), _dfpn_for_them(0.70))
    assert merged.status in {"unknown", "rejected"}
    assert any("hybrid_conflict" in n or "hybrid_hold_unknown" in n for n in merged.notes)


def test_hybrid_policy_mate_engine_first() -> None:
    adapter = MateAdapter("")
    adapter.verify_hybrid_policy = HYBRID_MATE_ENGINE_FIRST
    merged = adapter._merge_hybrid(_base_result(), _dfpn_for_them(0.95))
    assert merged.status == "confirmed"
    assert merged.mate_sign == "for_us"


def test_hybrid_policy_dfpn_first() -> None:
    adapter = MateAdapter("")
    adapter.verify_hybrid_policy = HYBRID_DFPN_FIRST
    merged = adapter._merge_hybrid(_base_result(), _dfpn_for_them(0.95))
    assert merged.status == "rejected"
    assert merged.mate_sign == "for_them"


if __name__ == "__main__":
    test_hybrid_agree_for_us_high_confidence()
    test_hybrid_conflict_conservative_returns_unknown_or_rejected()
    test_hybrid_policy_mate_engine_first()
    test_hybrid_policy_dfpn_first()
    print("ok test_verify_hybrid_phase5")
