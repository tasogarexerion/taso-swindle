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
    (
        "test_info_parser",
        [
            "test_parse_basic_cp_pv_multipv",
            "test_parse_mate_priority",
            "test_same_multipv_overwrite",
            "test_invalid_line_is_skipped",
        ],
    ),
    (
        "test_usi_messages",
        [
            "test_parse_setoption_with_spaces",
            "test_parse_setoption_without_value",
            "test_parse_mate_token_plus_minus",
            "test_bestmove_special_and_usi_move_boundaries",
        ],
    ),
    (
        "test_onlymove_gap",
        [
            "test_gap12_gap13_basic",
            "test_missing_reply_is_conservative",
            "test_mate_mixed_handling",
        ],
    ),
    (
        "test_reply_entropy",
        [
            "test_uniform_distribution_entropy_high",
            "test_skewed_distribution_entropy_low",
            "test_nan_guard",
        ],
    ),
    (
        "test_scoring",
        [
            "test_rev_breakdown_matches_total",
            "test_mode_difference_tactical_vs_murky",
            "test_mate_priority_flag_source",
        ],
    ),
    (
        "test_modes",
        [
            "test_auto_tactical_from_check_like",
            "test_auto_murky_from_tight_cp_spread",
            "test_auto_hybrid_fallback",
        ],
    ),
    (
        "test_trap_features",
        [
            "test_trap_not_zero",
        ],
    ),
    (
        "test_integration_mock_engine",
        [
            "test_controller_selects_rev_top_and_restores_multipv",
            "test_dryrun_keeps_backend_move",
            "test_restore_failed_is_event_level_and_logged",
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
                getattr(module, function_name)()
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
