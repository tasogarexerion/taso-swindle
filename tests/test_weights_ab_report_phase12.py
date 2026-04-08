from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path) -> None:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode != 0:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")


def test_weights_ab_hybrid_json_and_md() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s12-ab-") as td:
        d = Path(td)
        a = d / "a_hybrid.json"
        b = d / "b_hybrid.json"
        out = d / "report_hybrid.json"
        md = d / "report_hybrid.md"

        a.write_text(
            json.dumps(
                {
                    "kind": "hybrid_adjustment",
                    "features_version": "v2",
                    "label_mode": "pseudo",
                    "weights": {"bias": 0.0, "agree": 0.05, "conflict": -0.05},
                }
            ),
            encoding="utf-8",
        )
        b.write_text(
            json.dumps(
                {
                    "kind": "hybrid_adjustment",
                    "features_version": "v2",
                    "label_mode": "mixed",
                    "weights": {"bias": 0.01, "agree": 0.08, "new_key": 0.1},
                }
            ),
            encoding="utf-8",
        )

        _run(
            [
                sys.executable,
                "scripts/report_weights_ab.py",
                "--a",
                str(a),
                "--b",
                str(b),
                "--type",
                "hybrid",
                "--out",
                str(out),
                "--md-out",
                str(md),
            ],
            ROOT,
        )

        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["weights_type"] == "hybrid"
        assert "new_key" in rep["keys_added"]
        assert "conflict" in rep["keys_removed"]
        assert "label_mode" in rep["metadata_diff"]
        assert md.exists()
        assert "Weights A/B Report" in md.read_text(encoding="utf-8")


def test_weights_ab_ponder_json() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s12-ab-") as td:
        d = Path(td)
        a = d / "a_ponder.json"
        b = d / "b_ponder.json"
        out = d / "report_ponder.json"

        a.write_text(
            json.dumps(
                {
                    "kind": "ponder_gate_adjustment",
                    "features_version": "ponder-v1",
                    "weights": {"cache_age_ms": -0.04, "reply_coverage": 0.05},
                }
            ),
            encoding="utf-8",
        )
        b.write_text(
            json.dumps(
                {
                    "kind": "ponder_gate_adjustment",
                    "features_version": "ponder-v2",
                    "weights": {"cache_age_ms": 0.25, "reply_coverage": 0.03},
                }
            ),
            encoding="utf-8",
        )

        _run(
            [
                sys.executable,
                "scripts/report_weights_ab.py",
                "--a",
                str(a),
                "--b",
                str(b),
                "--type",
                "ponder",
                "--out",
                str(out),
            ],
            ROOT,
        )

        rep = json.loads(out.read_text(encoding="utf-8"))
        assert rep["weights_type"] == "ponder"
        assert "feature_version_mismatch" in rep["safety_notes"]
        assert "cache_age_ms" in rep["coef_deltas"]


if __name__ == "__main__":
    test_weights_ab_hybrid_json_and_md()
    test_weights_ab_ponder_json()
    print("ok test_weights_ab_report_phase12")
