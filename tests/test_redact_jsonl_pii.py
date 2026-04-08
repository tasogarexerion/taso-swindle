from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,S607
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )


def _sample_record() -> dict:
    return {
        "game_id": "wars://user-a-20260201-abc",
        "game_id_raw": "https://example/wars/user-a-20260201-abc?q=1",
        "game_id_normalized": "usera20260201abc",
        "source_log_path": "/tmp/private/source.jsonl",
        "_source_log_path": "/tmp/private/raw_source.jsonl",
        "backend_engine_info": {"name": "external_kifu", "author": "K_Yamawasabi"},
        "label": 1.0,
    }


def test_redact_output_dir_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-redact-") as td:
        base = Path(td)
        src = base / "in.jsonl"
        src.write_text(json.dumps(_sample_record(), ensure_ascii=False) + "\n", encoding="utf-8")
        out_dir = base / "out"
        summary = base / "summary.json"

        done = _run(
            [
                sys.executable,
                "scripts/redact_jsonl_pii.py",
                "--input",
                str(src),
                "--output-dir",
                str(out_dir),
                "--summary-out",
                str(summary),
            ]
        )
        assert done.returncode == 0, done.stderr
        out_files = list(out_dir.rglob("*.jsonl"))
        assert len(out_files) == 1
        rec = json.loads(out_files[0].read_text(encoding="utf-8").strip())
        for key in ("game_id", "game_id_raw", "game_id_normalized", "source_log_path", "_source_log_path"):
            assert rec[key].startswith(f"anon:{key}:")
        assert rec["backend_engine_info"]["author"].startswith("anon:backend_engine_info.author:")

        payload = json.loads(summary.read_text(encoding="utf-8"))
        assert payload["processed_files"] == 1
        assert payload["failed_files"] == 0
        assert payload["records_redacted"] == 1


def test_redact_in_place_mode_and_missing_fields() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-redact-") as td:
        base = Path(td)
        src = base / "in.jsonl"
        rows = [
            _sample_record(),
            {"label": 0.4, "note": "missing pii keys"},
        ]
        src.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n", encoding="utf-8")

        done = _run(
            [
                sys.executable,
                "scripts/redact_jsonl_pii.py",
                "--input",
                str(src),
                "--in-place",
                "--salt",
                "abc",
            ]
        )
        assert done.returncode == 0, done.stderr
        lines = [x for x in src.read_text(encoding="utf-8").splitlines() if x.strip()]
        rec0 = json.loads(lines[0])
        rec1 = json.loads(lines[1])
        assert rec0["game_id"].startswith("anon:game_id:")
        assert rec1["note"] == "missing pii keys"


if __name__ == "__main__":
    test_redact_output_dir_mode()
    test_redact_in_place_mode_and_missing_fields()
    print("ok test_redact_jsonl_pii")
