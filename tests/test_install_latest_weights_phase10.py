from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    return done.returncode, done.stdout, done.stderr


def _prepare_run(root: Path) -> Path:
    run = root / "20990101-000000"
    (run / "weights").mkdir(parents=True, exist_ok=True)
    (run / "weights" / "ponder_gate_weights.json").write_text('{"kind":"ponder_gate_adjustment"}', encoding="utf-8")
    (run / "weights" / "hybrid_weights.json").write_text('{"kind":"hybrid_adjustment"}', encoding="utf-8")
    (run / "summary.json").write_text(
        json.dumps({"status": "success", "run_id": "20990101-000000"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return run


def test_install_latest_weights_dry_run_and_copy() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s10-install-") as td:
        d = Path(td)
        artifacts = d / "artifacts"
        _prepare_run(artifacts)
        dst_dir = d / "models"
        dst_dir.mkdir(parents=True, exist_ok=True)
        summary_dry = d / "dry_summary.json"

        rc, _out, _err = _run(
            [
                sys.executable,
                "scripts/install_latest_weights.py",
                "--artifacts-root",
                str(artifacts),
                "--ponder-dst",
                str(dst_dir / "ponder.json"),
                "--hybrid-dst",
                str(dst_dir / "hybrid.json"),
                "--dry-run",
                "--summary-out",
                str(summary_dry),
            ],
            ROOT,
        )
        assert rc == 0
        assert summary_dry.exists()
        dry = json.loads(summary_dry.read_text(encoding="utf-8"))
        assert dry["dry_run"] is True
        assert not (dst_dir / "ponder.json").exists()

        summary_real = d / "real_summary.json"
        rc, _out, _err = _run(
            [
                sys.executable,
                "scripts/install_latest_weights.py",
                "--artifacts-root",
                str(artifacts),
                "--ponder-dst",
                str(dst_dir / "ponder.json"),
                "--hybrid-dst",
                str(dst_dir / "hybrid.json"),
                "--summary-out",
                str(summary_real),
            ],
            ROOT,
        )
        assert rc == 0
        assert (dst_dir / "ponder.json").exists()
        assert (dst_dir / "hybrid.json").exists()
        real = json.loads(summary_real.read_text(encoding="utf-8"))
        assert real["status"] in {"success", "partial"}


if __name__ == "__main__":
    test_install_latest_weights_dry_run_and_copy()
    print("ok test_install_latest_weights_phase10")
