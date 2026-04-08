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


def _seed_samples(path: Path) -> None:
    rows = [
        {"parse_status": "unknown", "unknown_reason": "parse_unknown", "raw_summary": "no mate found in 7 ply"},
        {"parse_status": "unknown", "unknown_reason": "parse_unknown", "raw_summary": "不詰 12手 詰まず"},
        {"parse_status": "error", "unknown_reason": "subprocess_error", "raw_summary": "legacy output token abc"},
    ]
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_build_candidates_with_features_out() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s12-dfpn-feat-") as td:
        d = Path(td)
        inp = d / "samples.jsonl"
        cand = d / "candidates.json"
        feats = d / "features.jsonl"
        _seed_samples(inp)

        _run(
            [
                sys.executable,
                "scripts/build_dfpn_pack_candidates.py",
                "--input",
                str(inp),
                "--output",
                str(cand),
                "--min-support",
                "1",
                "--features-out",
                str(feats),
            ],
            ROOT,
        )

        payload = json.loads(cand.read_text(encoding="utf-8"))
        assert payload["kind"] == "dfpn_pack_candidates"
        assert isinstance(payload.get("proposals"), list)
        assert payload["proposals"]

        rows = [json.loads(line) for line in feats.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows
        sample = rows[0]
        for key in ["token", "token_len", "has_digit", "language", "negation_hit", "distance_hit", "token_class", "sample_count"]:
            assert key in sample


if __name__ == "__main__":
    test_build_candidates_with_features_out()
    print("ok test_dfpn_candidate_features_phase12")
