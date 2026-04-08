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


def _sample_event() -> dict:
    return {
        "timestamp": "2026-03-01T00:00:00+00:00",
        "game_id": "wars://K_Yamawasabi-20260201-abc",
        "ply": 15,
        "search_id": 77,
        "final_bestmove": "7g7f",
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "unknown",
        "candidates": [{"move": "7g7f", "reply_topk": [{"move": "3c3d"}]}],
        "actual_opponent_move": "3c3d",
        "actual_move_in_reply_topk": True,
        "actual_move_rank_in_reply_topk": 1,
        "outcome_tag": "swing_success",
        "outcome_confidence": 0.9,
        "game_id_raw": "https://example.test/wars/K_Yamawasabi-20260201-abc?k=v",
        "game_id_normalized": "kyamawasabi20260201abc",
        "_source_log_path": "/Users/taso/private/path/logs.jsonl",
    }


def test_external_kifu_fetch_scripts_are_blocked() -> None:
    extract = _run(
        [
            sys.executable,
            "scripts/extract_shogi_extend_user_kifu.py",
            "--user-key",
            "dummy",
            "--max-games",
            "1",
        ]
    )
    assert extract.returncode != 0
    assert "policy_blocked" in (extract.stderr + extract.stdout)

    collect = _run(
        [
            sys.executable,
            "scripts/collect_shogi_extend_highdan_corpus.py",
            "--seed-user",
            "dummy",
            "--max-users",
            "1",
        ]
    )
    assert collect.returncode != 0
    assert "policy_blocked" in (collect.stderr + collect.stdout)


def test_build_training_labels_anonymizes_personal_info_by_default() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-comply-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        out_path = d / "out.jsonl"
        in_path.write_text(json.dumps(_sample_event(), ensure_ascii=False) + "\n", encoding="utf-8")

        done = _run(
            [
                sys.executable,
                "scripts/build_training_labels.py",
                "--input",
                str(in_path),
                "--output",
                str(out_path),
                "--label-mode",
                "mixed",
            ]
        )
        assert done.returncode == 0, done.stderr

        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        for key in ("game_id", "game_id_raw", "game_id_normalized", "source_log_path"):
            assert isinstance(rec[key], str)
            assert rec[key].startswith(f"anon:{key}:")
            assert "yamawasabi" not in rec[key].lower()


def test_build_training_labels_can_disable_anonymize() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-comply-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        out_path = d / "out.jsonl"
        ev = _sample_event()
        in_path.write_text(json.dumps(ev, ensure_ascii=False) + "\n", encoding="utf-8")

        done = _run(
            [
                sys.executable,
                "scripts/build_training_labels.py",
                "--input",
                str(in_path),
                "--output",
                str(out_path),
                "--label-mode",
                "mixed",
                "--no-anonymize-personal-info",
            ]
        )
        assert done.returncode == 0, done.stderr
        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert rec["game_id"] == ev["game_id"]
        assert rec["game_id_raw"] == ev["game_id_raw"]
        assert rec["game_id_normalized"] == ev["game_id_normalized"]
        assert rec["source_log_path"] == ev["_source_log_path"]


if __name__ == "__main__":
    test_external_kifu_fetch_scripts_are_blocked()
    test_build_training_labels_anonymizes_personal_info_by_default()
    test_build_training_labels_can_disable_anonymize()
    print("ok test_compliance_rules")
