from __future__ import annotations

import importlib
import traceback
from pathlib import Path
import sys

TEST_DIR = Path(__file__).resolve().parent
ROOT = TEST_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))


TEST_CALLS: list[tuple[str, list[str]]] = [
    ("run_phase6_checks", ["main"]),
    (
        "test_dfpn_dialect_pack_phase7",
        [
            "test_auto_selects_japanese_pack",
            "test_auto_selects_legacy_pack",
            "test_forced_dialect_uses_requested_pack",
            "test_source_detail_contains_dialect_and_mode",
            "test_unknown_dialect_falls_back_auto",
            "test_parser_failure_still_returns_unknown",
        ],
    ),
    (
        "test_hybrid_supervised_phase7",
        [
            "test_build_training_labels_matches_actual_move_rank",
            "test_train_supervised_outputs_metadata",
            "test_train_mixed_falls_back_to_pseudo",
            "test_weight_loader_feature_version_mismatch_noop",
            "test_runtime_adjustment_supervised_stays_capped",
            "test_mate_sign_not_flipped_by_learning",
        ],
    ),
    (
        "test_ponder_gate_phase7",
        [
            "test_reuse_score_high_with_good_coverage",
            "test_reuse_score_low_with_stale_cache",
            "test_gate_blocks_low_quality_cache",
            "test_gate_blocks_mate_signal_without_verify_when_required",
            "test_gate_accepts_good_cache",
            "test_event_level_gate_reason_recorded",
        ],
    ),
    (
        "test_usi_protocol_e2e_mock",
        [
            "test_ponderhit_quality_gate_allows_reuse",
            "test_ponderhit_quality_gate_blocks_reuse_but_no_hang",
        ],
    ),
]


def main() -> int:
    failures: list[str] = []
    total = 0
    for module_name, function_names in TEST_CALLS:
        module = importlib.import_module(module_name)
        for function_name in function_names:
            total += 1
            try:
                rc = getattr(module, function_name)()
                if function_name == "main" and isinstance(rc, int) and rc != 0:
                    raise RuntimeError(f"{module_name}.{function_name} returned {rc}")
            except Exception:
                failures.append(f"{module_name}.{function_name}")
                traceback.print_exc()

    if failures:
        print(f"FAILED {len(failures)}/{total}")
        for name in failures:
            print(f" - {name}")
        return 1

    print(f"OK {total}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
