from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from game_id_normalizer import detect_game_id_source, normalize_game_id


def _run(cmd: list[str], cwd: Path) -> None:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode != 0:
        raise AssertionError(f"rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")


def test_normalize_game_id_url_and_query() -> None:
    raw = "https://example.com/game/view?game_id=WAR-12_AbC.csa&foo=1"
    assert normalize_game_id(raw, None) == "war12abc"


def test_normalize_game_id_separator_absorb() -> None:
    assert normalize_game_id("WAR-12_ABC", "wars") == normalize_game_id("war/12:abc", "wars")


def test_detect_game_id_source() -> None:
    assert detect_game_id_source("https://shogiwars.heroz.jp/game/123") == "wars"
    assert detect_game_id_source("https://81dojo.com/xxxx") == "81dojo"
    assert detect_game_id_source("abc123") == "unknown"


def test_fill_kif_emits_game_id_normalized_exact() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s12-gid-") as td:
        d = Path(td)
        inp = d / "in.jsonl"
        kif_dir = d / "kif"
        kif_dir.mkdir(parents=True, exist_ok=True)
        out = d / "out.jsonl"

        rec = {
            "timestamp": "2026-02-25T00:00:00.000+00:00",
            "game_id": "https://shogiwars.heroz.jp/game?gid=WAR-12_ABC",
            "ply": 2,
            "root_eval_cp": -500,
            "final_bestmove": "2g2f",
            "selected_reason": "rev_max",
            "verify_status_summary": "confirmed",
            "dfpn_status_summary": "confirmed",
            "candidates": [{"move": "2g2f", "base_cp": -250, "reply_topk": [{"move": "8c8d"}]}],
        }
        inp.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
        (kif_dir / "war12abc.kif").write_text("1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/fill_outcomes_from_kif.py",
                "--input",
                str(inp),
                "--kif-dir",
                str(kif_dir),
                "--output",
                str(out),
            ],
            ROOT,
        )
        row = json.loads(out.read_text(encoding="utf-8").strip())
        assert row["outcome_match_source"] in {"game_id_normalized_exact", "game_id_exact"}
        assert row["game_id_raw"].startswith("https://")
        assert row["game_id_normalized"] == "war12abc"
        assert row["game_id_source_detected"] == "wars"


if __name__ == "__main__":
    test_normalize_game_id_url_and_query()
    test_normalize_game_id_separator_absorb()
    test_detect_game_id_source()
    test_fill_kif_emits_game_id_normalized_exact()
    print("ok test_game_id_normalizer_phase12")
