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
    ("run_phase12_checks", ["main"]),
    (
        "test_learning_pipeline_hash_diff_phase13",
        ["test_resume_hash_detects_input_change"],
    ),
    (
        "test_weights_ab_report_phase13",
        ["test_weights_ab_with_actual_game_eval_diff"],
    ),
    (
        "test_dfpn_token_classifier_phase13",
        ["test_train_and_apply_dfpn_token_classifier"],
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
