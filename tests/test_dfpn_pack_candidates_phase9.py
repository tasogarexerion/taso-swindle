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


def _write_unknown_samples(path: Path) -> None:
    rows = [
        {
            "parse_status": "unknown",
            "unknown_reason": "parse_unknown",
            "raw_summary": "legacy format found matewin token AAAA",
        },
        {
            "parse_status": "unknown",
            "unknown_reason": "parse_unknown",
            "raw_summary": "legacy format found matewin token BBBB",
        },
        {
            "parse_status": "error",
            "unknown_reason": "subprocess_error",
            "raw_summary": "legacy format timeout matewin",
        },
    ]
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_dfpn_pack_candidates_builds_proposals_from_unknowns() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s9-dfpn-") as td:
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
                "--min-count",
                "2",
            ],
            ROOT,
        )
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["kind"] == "dfpn_pack_candidates"
        assert payload["unknown_records"] >= 1
        assert isinstance(payload.get("proposals"), list)
        assert payload["proposals"]


def test_dfpn_pack_candidates_never_overwrites_default_pack() -> None:
    default_pack = ROOT / "dfpn_dialects" / "default_packs.json"
    before = default_pack.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="taso-s9-dfpn-") as td:
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
            ],
            ROOT,
        )
    after = default_pack.read_text(encoding="utf-8")
    assert before == after


if __name__ == "__main__":
    test_dfpn_pack_candidates_builds_proposals_from_unknowns()
    test_dfpn_pack_candidates_never_overwrites_default_pack()
    print("ok test_dfpn_pack_candidates_phase9")
