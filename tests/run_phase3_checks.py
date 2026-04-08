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
        "run_phase2_checks",
        [
            "main",
        ],
    ),
    (
        "test_usi_protocol_e2e_mock",
        [
            "test_usi_go_dryrun_true",
            "test_usi_go_dryrun_false",
            "test_verify_timeout_continues",
            "test_long_go_stop_returns_bestmove",
            "test_ponderhit_no_hang",
            "test_backend_restart_chain_fallback",
            "test_verify_and_dfpn_timeout_still_returns_bestmove",
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
