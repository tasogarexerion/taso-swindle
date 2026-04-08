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
    ("run_phase11_checks", ["main"]),
    (
        "test_game_id_normalizer_phase12",
        [
            "test_normalize_game_id_url_and_query",
            "test_normalize_game_id_separator_absorb",
            "test_detect_game_id_source",
            "test_fill_kif_emits_game_id_normalized_exact",
        ],
    ),
    (
        "test_learning_pipeline_resume_phase12",
        [
            "test_resume_and_force_stage",
            "test_retry_records_stage_attempts",
        ],
    ),
    (
        "test_weights_ab_report_phase12",
        [
            "test_weights_ab_hybrid_json_and_md",
            "test_weights_ab_ponder_json",
        ],
    ),
    (
        "test_dfpn_candidate_features_phase12",
        [
            "test_build_candidates_with_features_out",
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
