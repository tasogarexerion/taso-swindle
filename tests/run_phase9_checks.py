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
    ("run_phase8_checks", ["main"]),
    (
        "test_ponder_runtime_label_phase9",
        [
            "test_ponder_runtime_label_logged_on_cache_hit",
            "test_ponder_runtime_label_false_when_same_move",
            "test_ponder_runtime_label_fallback_to_heuristic_when_missing",
        ],
    ),
    (
        "test_build_training_labels_ponder_phase9",
        [
            "test_build_labels_runtime_first_prefers_runtime",
            "test_build_labels_min_ponder_label_confidence_filters",
        ],
    ),
    (
        "test_train_ponder_gate_phase9",
        [
            "test_train_ponder_gate_uses_source_weighting",
            "test_train_ponder_gate_metadata_written",
        ],
    ),
    (
        "test_dfpn_pack_candidates_phase9",
        [
            "test_dfpn_pack_candidates_builds_proposals_from_unknowns",
            "test_dfpn_pack_candidates_never_overwrites_default_pack",
        ],
    ),
    (
        "test_usi_protocol_e2e_mock",
        [
            "test_usi_protocol_e2e_runtime_label_does_not_break_bestmove",
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
