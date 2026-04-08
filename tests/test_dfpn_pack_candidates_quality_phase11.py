from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode != 0:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")
    return done


def _write_unknown_samples(path: Path) -> None:
    rows = [
        {
            "parse_status": "unknown",
            "unknown_reason": "parse_unknown",
            "raw_summary": "no mate found in 7 ply",
        },
        {
            "parse_status": "unknown",
            "unknown_reason": "parse_unknown",
            "raw_summary": "not found mate in 8 ply",
        },
        {
            "parse_status": "unknown",
            "unknown_reason": "parse_unknown",
            "raw_summary": "不詰 12手 詰まず",
        },
        {
            "parse_status": "unknown",
            "unknown_reason": "parse_unknown",
            "raw_summary": "詰み 発見 6手",
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_candidate_quality_fields_exist() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-dfpn-") as td:
        d = Path(td)
        inp = d / "samples.jsonl"
        out = d / "candidates.json"
        _write_unknown_samples(inp)

        _run(
            [
                sys.executable,
                "scripts/build_dfpn_pack_candidates.py",
                "--input",
                str(inp),
                "--output",
                str(out),
                "--min-support",
                "1",
                "--language",
                "mixed",
                "--with-negation",
                "--with-distance",
            ],
            ROOT,
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["kind"] == "dfpn_pack_candidates"
        assert isinstance(payload.get("proposals"), list) and payload["proposals"]
        proposal = payload["proposals"][0]
        assert "token_class" in proposal
        assert "sample_count" in proposal
        assert "confidence_hint" in proposal
        assert "examples" in proposal


def test_language_and_switches_affect_output() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-dfpn-") as td:
        d = Path(td)
        inp = d / "samples.jsonl"
        out = d / "candidates.json"
        _write_unknown_samples(inp)

        _run(
            [
                sys.executable,
                "scripts/build_dfpn_pack_candidates.py",
                "--input",
                str(inp),
                "--output",
                str(out),
                "--language",
                "ja",
                "--min-support",
                "1",
                "--with-negation",
                "--no-with-distance",
            ],
            ROOT,
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        proposals = payload.get("proposals", [])
        assert proposals
        assert all((p.get("language") in {"ja", "mixed"}) for p in proposals)
        assert all((p.get("token_class") != "distance") for p in proposals)


if __name__ == "__main__":
    test_candidate_quality_fields_exist()
    test_language_and_switches_affect_output()
    print("ok test_dfpn_pack_candidates_quality_phase11")
