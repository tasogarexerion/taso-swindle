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
    ("run_phase5_checks", ["main"]),
    (
        "test_dfpn_parser_phase6",
        [
            "test_parse_strict_for_us_japanese_text",
            "test_parse_strict_for_them_english_text",
            "test_parse_loose_hint_mode_auto",
            "test_parse_strict_rejects_loose",
            "test_distance_extract_in_ply",
            "test_distance_extract_in_te",
            "test_unknown_format_returns_unknown_with_summary",
            "test_confidence_clamped",
        ],
    ),
    (
        "test_hybrid_learning_phase6",
        [
            "test_weight_loader_missing_file_noop",
            "test_weight_loader_valid_file",
            "test_hybrid_adjustment_clamped",
            "test_mate_adapter_hybrid_with_learned_adjustment_safe_cap",
            "test_learning_disabled_keeps_rule_based",
        ],
    ),
    (
        "test_ponder_quality_phase6",
        [
            "test_ponder_cache_hit_reuses_snapshot",
            "test_ponder_cache_miss_discards_snapshot",
            "test_ponder_verify_off_restores_state",
            "test_ponder_dfpn_off_restores_state",
            "test_ponder_budget_recorded_event_level",
        ],
    ),
    (
        "test_usi_protocol_e2e_mock",
        [
            "test_ponderhit_cache_hit_no_hang",
            "test_ponder_crash_fallback_keeps_bestmove",
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
