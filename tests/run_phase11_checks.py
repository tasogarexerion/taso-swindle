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
    ("run_phase10_checks", ["main"]),
    (
        "test_outcome_fill_matching_phase11",
        [
            "test_kif_multi_file_strict_match_prefers_game_id",
            "test_csa_multi_file_strict_match_prefers_game_id",
            "test_unmatched_keeps_pipeline_safe",
        ],
    ),
    (
        "test_learning_pipeline_multilog_phase11",
        [
            "test_multilog_summary_has_input_sources",
            "test_failed_stage_and_partial_outputs_on_fill_failure",
        ],
    ),
    (
        "test_dfpn_pack_candidates_quality_phase11",
        [
            "test_candidate_quality_fields_exist",
            "test_language_and_switches_affect_output",
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
