from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path, ok_codes: set[int] | None = None) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    allowed = ok_codes if ok_codes is not None else {0}
    if done.returncode not in allowed:
        raise AssertionError(f"command failed rc={done.returncode}\nSTDOUT:\n{done.stdout}\nSTDERR:\n{done.stderr}")
    return done


def _event(*, game_id: str = "g2", ply: int = 2, final: str = "2g2f") -> dict:
    return {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": game_id,
        "ply": ply,
        "search_id": 10,
        "root_eval_cp": -500,
        "final_bestmove": final,
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "confirmed",
        "candidates": [
            {
                "move": final,
                "base_cp": -250,
                "reply_topk": [
                    {"move": "3c3d"},
                    {"move": "8c8d"},
                    {"move": "4c4d"},
                ],
            }
        ],
    }


def test_kif_multi_file_strict_match_prefers_game_id() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-kif-") as td:
        d = Path(td)
        input_path = d / "in.jsonl"
        kif_dir = d / "kif"
        kif_dir.mkdir(parents=True, exist_ok=True)
        out_path = d / "out.jsonl"

        input_path.write_text(json.dumps(_event(game_id="match-2")) + "\n", encoding="utf-8")

        (kif_dir / "game-a.kif").write_text(
            "開始日時：2026/02/25\n1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n",
            encoding="utf-8",
        )
        (kif_dir / "match-2.kif").write_text(
            "開始日時：2026/02/25\n1 7g7f\n2 3c3d\n3 2g2f\n4 4c4d\n",
            encoding="utf-8",
        )

        _run(
            [
                sys.executable,
                "scripts/fill_outcomes_from_kif.py",
                "--input",
                str(input_path),
                "--kif-dir",
                str(kif_dir),
                "--output",
                str(out_path),
            ],
            ROOT,
        )

        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert rec["actual_opponent_move"] == "4c4d"
        assert rec["outcome_match_source"] == "game_id_exact"
        assert float(rec["outcome_match_confidence"]) >= 0.7
        assert int(rec["outcome_match_candidates"]) >= 1


def test_csa_multi_file_strict_match_prefers_game_id() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-csa-") as td:
        d = Path(td)
        input_path = d / "in.jsonl"
        csa_dir = d / "csa"
        csa_dir.mkdir(parents=True, exist_ok=True)
        out_path = d / "out.jsonl"

        input_path.write_text(json.dumps(_event(game_id="csa-target")) + "\n", encoding="utf-8")

        (csa_dir / "a.csa").write_text(
            "N+Sente\nN-Gote\n+7776FU\n-3334FU\n+2726FU\n-8384FU\n",
            encoding="utf-8",
        )
        (csa_dir / "csa-target.csa").write_text(
            "$GAME_ID:csa-target\nN+Sente\nN-Gote\n+7776FU\n-3334FU\n+2726FU\n-4142KI\n",
            encoding="utf-8",
        )

        _run(
            [
                sys.executable,
                "scripts/fill_outcomes_from_csa.py",
                "--input",
                str(input_path),
                "--csa-dir",
                str(csa_dir),
                "--output",
                str(out_path),
            ],
            ROOT,
        )

        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert rec["actual_opponent_move"] == "4a4b"
        assert rec["outcome_match_source"] == "game_id_exact"
        assert float(rec["outcome_match_confidence"]) >= 0.7


def test_unmatched_keeps_pipeline_safe() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s11-unmatched-") as td:
        d = Path(td)
        input_path = d / "in.jsonl"
        kif_dir = d / "kif"
        kif_dir.mkdir(parents=True, exist_ok=True)
        out_path = d / "out.jsonl"

        input_path.write_text(json.dumps(_event(game_id="missing", final="9i9h")) + "\n", encoding="utf-8")
        (kif_dir / "x.kif").write_text("1 7g7f\n2 3c3d\n", encoding="utf-8")

        _run(
            [
                sys.executable,
                "scripts/fill_outcomes_from_kif.py",
                "--input",
                str(input_path),
                "--kif-dir",
                str(kif_dir),
                "--output",
                str(out_path),
            ],
            ROOT,
        )

        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert rec["outcome_match_source"] == "unmatched"
        assert rec["actual_opponent_move"] is None
        assert rec["outcome_tag"] in {"unknown", "neutral", "swing_fail", "swing_success"}


if __name__ == "__main__":
    test_kif_multi_file_strict_match_prefers_game_id()
    test_csa_multi_file_strict_match_prefers_game_id()
    test_unmatched_keeps_pipeline_safe()
    print("ok test_outcome_fill_matching_phase11")
