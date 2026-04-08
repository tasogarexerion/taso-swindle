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
    ("run_phase4_checks", ["main"]),
    (
        "test_dfpn_parser_phase5",
        [
            "test_dfpn_parse_mate_for_us_with_distance",
            "test_dfpn_parse_mate_for_them_with_distance",
            "test_dfpn_parse_unknown_format_returns_unknown",
            "test_dfpn_parser_strict_vs_loose",
        ],
    ),
    (
        "test_verify_hybrid_phase5",
        [
            "test_hybrid_agree_for_us_high_confidence",
            "test_hybrid_conflict_conservative_returns_unknown_or_rejected",
            "test_hybrid_policy_mate_engine_first",
            "test_hybrid_policy_dfpn_first",
        ],
    ),
    (
        "test_usi_protocol_e2e_mock",
        [
            "test_ponderhit_no_hang",
            "test_ponder_timeout_event_only",
            "test_ponder_backend_crash_fallback_bestmove",
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
