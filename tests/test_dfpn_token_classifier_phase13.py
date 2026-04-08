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


def test_train_and_apply_dfpn_token_classifier() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s13-cls-") as td:
        d = Path(td)
        features = d / "features.jsonl"
        model = d / "model.json"
        samples = d / "samples.jsonl"
        out = d / "candidates.json"

        rows = [
            {"token": "zugzwang", "sample_count": 5, "token_len": 8, "has_digit": False, "digit_count": 0, "ja_ratio": 0.0, "en_ratio": 1.0, "negation_hit": False, "distance_hit": False, "token_class_label": "mate_positive", "token_class": "unknown_marker", "context_freq": 4},
            {"token": "nomate", "sample_count": 4, "token_len": 6, "has_digit": False, "digit_count": 0, "ja_ratio": 0.0, "en_ratio": 1.0, "negation_hit": True, "distance_hit": False, "token_class_label": "mate_negative", "token_class": "mate_negative", "context_freq": 3},
            {"token": "12ply", "sample_count": 3, "token_len": 5, "has_digit": True, "digit_count": 2, "ja_ratio": 0.0, "en_ratio": 0.6, "negation_hit": False, "distance_hit": True, "token_class_label": "distance", "token_class": "distance", "context_freq": 2},
            {"token": "noise", "sample_count": 3, "token_len": 5, "has_digit": False, "digit_count": 0, "ja_ratio": 0.0, "en_ratio": 1.0, "negation_hit": False, "distance_hit": False, "token_class_label": "unknown_marker", "token_class": "unknown_marker", "context_freq": 1},
        ]
        with features.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        _run(
            [
                sys.executable,
                "scripts/train_dfpn_token_classifier.py",
                "--input",
                str(features),
                "--input-format",
                "jsonl",
                "--output",
                str(model),
                "--min-samples",
                "1",
            ],
            ROOT,
        )

        sample_rows = [
            {"parse_status": "unknown", "unknown_reason": "parse_unknown", "raw_summary": "zugzwang found"},
            {"parse_status": "unknown", "unknown_reason": "parse_unknown", "raw_summary": "nomate notfound"},
        ]
        with samples.open("w", encoding="utf-8") as fh:
            for row in sample_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        _run(
            [
                sys.executable,
                "scripts/build_dfpn_pack_candidates.py",
                "--input",
                str(samples),
                "--output",
                str(out),
                "--min-support",
                "1",
                "--classifier-model",
                str(model),
                "--classifier-min-confidence",
                "0.0",
            ],
            ROOT,
        )

        payload = json.loads(out.read_text(encoding="utf-8"))
        proposals = [p for p in payload.get("proposals", []) if p.get("token") == "zugzwang"]
        assert proposals
        p = proposals[0]
        assert p["token_class_source"] == "model"
        assert isinstance(p.get("token_class_predicted"), str) and p["token_class_predicted"]
        assert p["token_class"] in {"mate_positive", "mate_negative", "distance", "unknown_marker"}


if __name__ == "__main__":
    test_train_and_apply_dfpn_token_classifier()
    print("ok test_dfpn_token_classifier_phase13")
