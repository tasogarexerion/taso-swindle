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
    ("run_phase7_checks", ["main"]),
    (
        "test_dfpn_dialect_external_phase8",
        [
            "test_external_pack_load_success",
            "test_external_pack_invalid_regex_partial_fallback",
            "test_external_pack_all_invalid_falls_back_builtin",
            "test_forced_pack_path_missing_falls_back_builtin",
            "test_event_logs_pack_source_and_version",
            "test_source_detail_normalization_still_works",
        ],
    ),
    (
        "test_outcome_fill_phase8",
        [
            "test_fill_actual_opponent_move_from_kif",
            "test_fill_rank_in_reply_topk",
            "test_outcome_tag_swing_success",
            "test_outcome_tag_unknown_when_insufficient",
            "test_build_training_labels_prefers_labeled_outcome",
            "test_labeled_output_does_not_modify_original",
        ],
    ),
    (
        "test_ponder_gate_learning_phase8",
        [
            "test_train_ponder_gate_outputs_metadata",
            "test_runtime_ponder_gate_adjustment_capped",
            "test_version_mismatch_noop",
            "test_learned_adjustment_does_not_force_reuse_when_gate_hard_fail",
            "test_event_logs_adjustment_fields",
            "test_rule_base_still_works_without_weights",
        ],
    ),
    (
        "test_usi_protocol_e2e_mock",
        [
            "test_ponderhit_learned_adjustment_path_no_hang",
            "test_ponderhit_quality_gate_hard_fail_ignores_learning",
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
