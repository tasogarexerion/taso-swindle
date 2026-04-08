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
    completed = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if completed.returncode != 0:
        raise AssertionError(f"command failed rc={completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}")


def _event(*, ply: int = 2, final: str = "2g2f", root_cp: int = -500, base_cp: int = -260) -> dict:
    return {
        "timestamp": "2026-02-25T00:00:00.000+00:00",
        "game_id": "g1",
        "ply": ply,
        "search_id": 10,
        "root_eval_cp": root_cp,
        "final_bestmove": final,
        "selected_reason": "rev_max",
        "verify_status_summary": "confirmed",
        "dfpn_status_summary": "confirmed",
        "candidates": [
            {
                "move": final,
                "base_cp": base_cp,
                "reply_topk": [
                    {"move": "3c3d"},
                    {"move": "8c8d"},
                    {"move": "4c4d"},
                ],
            }
        ],
    }


def test_fill_actual_opponent_move_from_kif() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-fill-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        kif_path = d / "game.kif"
        out_path = d / "out.labeled.jsonl"

        in_path.write_text(json.dumps(_event()), encoding="utf-8")
        kif_path.write_text("1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n", encoding="utf-8")

        _run([sys.executable, "scripts/fill_outcomes_from_kif.py", "--input", str(in_path), "--kif", str(kif_path), "--output", str(out_path)], ROOT)

        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert rec["actual_opponent_move"] == "8c8d"


def test_fill_rank_in_reply_topk() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-fill-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        kif_path = d / "game.kif"
        out_path = d / "out.labeled.jsonl"

        in_path.write_text(json.dumps(_event()), encoding="utf-8")
        kif_path.write_text("1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n", encoding="utf-8")
        _run([sys.executable, "scripts/fill_outcomes_from_kif.py", "--input", str(in_path), "--kif", str(kif_path), "--output", str(out_path)], ROOT)
        rec = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert rec["actual_move_in_reply_topk"] is True
        assert rec["actual_move_rank_in_reply_topk"] == 2


def test_outcome_tag_swing_success() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-fill-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        kif_path = d / "game.kif"
        out_path = d / "out.labeled.jsonl"

        rec = _event(base_cp=-200)
        rec["candidates"][0]["reply_topk"] = [{"move": "3c3d"}, {"move": "4c4d"}]
        in_path.write_text(json.dumps(rec), encoding="utf-8")
        kif_path.write_text("1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n", encoding="utf-8")

        _run([sys.executable, "scripts/fill_outcomes_from_kif.py", "--input", str(in_path), "--kif", str(kif_path), "--output", str(out_path)], ROOT)
        out = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert out["outcome_tag"] == "swing_success"
        assert out["outcome_confidence"] >= 0.6


def test_outcome_tag_unknown_when_insufficient() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-fill-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        kif_path = d / "game.kif"
        out_path = d / "out.labeled.jsonl"

        in_path.write_text(json.dumps(_event(ply=10)), encoding="utf-8")
        kif_path.write_text("1 7g7f\n2 3c3d\n", encoding="utf-8")
        _run([sys.executable, "scripts/fill_outcomes_from_kif.py", "--input", str(in_path), "--kif", str(kif_path), "--output", str(out_path)], ROOT)
        out = json.loads(out_path.read_text(encoding="utf-8").strip())
        assert out["outcome_tag"] == "unknown"


def test_build_training_labels_prefers_labeled_outcome() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-fill-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        kif_path = d / "game.kif"
        labeled = d / "out.labeled.jsonl"
        train_labels = d / "train.labels.jsonl"

        in_path.write_text(json.dumps(_event(base_cp=-180)), encoding="utf-8")
        kif_path.write_text("1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n", encoding="utf-8")

        _run([sys.executable, "scripts/fill_outcomes_from_kif.py", "--input", str(in_path), "--kif", str(kif_path), "--output", str(labeled)], ROOT)
        _run(
            [
                sys.executable,
                "scripts/build_training_labels.py",
                "--input",
                str(labeled),
                "--output",
                str(train_labels),
                "--label-mode",
                "mixed",
                "--prefer-labeled-outcome",
                "--min-outcome-confidence",
                "0.5",
                "--require-actual-move",
            ],
            ROOT,
        )

        out = json.loads(train_labels.read_text(encoding="utf-8").strip())
        assert out["label_source"] in {"supervised_labeled", "supervised"}


def test_labeled_output_does_not_modify_original() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-fill-") as td:
        d = Path(td)
        in_path = d / "in.jsonl"
        before = json.dumps(_event(), ensure_ascii=False)
        in_path.write_text(before + "\n", encoding="utf-8")

        kif_path = d / "game.kif"
        kif_path.write_text("1 7g7f\n2 3c3d\n3 2g2f\n4 8c8d\n", encoding="utf-8")
        out_path = d / "out.labeled.jsonl"
        _run([sys.executable, "scripts/fill_outcomes_from_kif.py", "--input", str(in_path), "--kif", str(kif_path), "--output", str(out_path)], ROOT)

        original_after = in_path.read_text(encoding="utf-8").strip()
        assert original_after == before


if __name__ == "__main__":
    test_fill_actual_opponent_move_from_kif()
    test_fill_rank_in_reply_topk()
    test_outcome_tag_swing_success()
    test_outcome_tag_unknown_when_insufficient()
    test_build_training_labels_prefers_labeled_outcome()
    test_labeled_output_does_not_modify_original()
    print("ok test_outcome_fill_phase8")
