from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _run(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str, str]:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    return done.returncode, done.stdout, done.stderr


def _seed_min_project(root: Path, nn_size_bytes: int = 1024 * 1024) -> None:
    (root / "eval").mkdir(parents=True, exist_ok=True)
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "taso_swindle").mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "YaneuraOu").write_bytes(b"fake-engine\n")
    (root / "eval" / "nn.bin").write_bytes(b"n" * nn_size_bytes)
    (root / "models" / "hybrid_weights.json").write_text('{"kind":"hybrid_adjustment"}\n', encoding="utf-8")

    (root / "taso_swindle" / "__init__.py").write_text("__all__ = []\n", encoding="utf-8")
    (root / "taso_swindle" / "main.py").write_text(
        "def main():\n    return 0\n\nif __name__ == '__main__':\n    raise SystemExit(main())\n",
        encoding="utf-8",
    )

    # Must never be included in release bundle.
    (root / "logs" / "raw_learning.jsonl").write_text('{"game_id":"secret"}\n', encoding="utf-8")
    (root / "tests" / "sample.kif").write_text("dummy\n", encoding="utf-8")
    (root / "artifacts" / "history_report.txt").write_text("contains report token\n", encoding="utf-8")


def _build_release(project_root: Path, output_root: Path, run_id: str, part_size_mb: int = 1) -> Path:
    rc, out, err = _run(
        [
            sys.executable,
            "scripts/build_discord_release.py",
            "--project-root",
            str(project_root),
            "--output-root",
            str(output_root),
            "--run-id",
            run_id,
            "--part-size-mb",
            str(part_size_mb),
            "--strict-scan",
        ]
    )
    if rc != 0:
        raise AssertionError(f"build failed rc={rc}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    run_dir = output_root / run_id
    assert run_dir.exists()
    return run_dir


def test_release_profile_flavor_with_hybrid() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-discord-rel-") as td:
        d = Path(td)
        project = d / "project"
        out = d / "out"
        _seed_min_project(project)
        run_dir = _build_release(project, out, "r1", part_size_mb=1)

        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["profile"] == "flavor_with_hybrid"
        assert manifest["privacy_ok"] is True
        assert (run_dir / "bundle" / "models" / "hybrid_weights.json").exists()


def test_release_excludes_logs_kifu_jsonl() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-discord-rel-") as td:
        d = Path(td)
        project = d / "project"
        out = d / "out"
        _seed_min_project(project)
        run_dir = _build_release(project, out, "r2", part_size_mb=1)

        bundle_files = [p.relative_to(run_dir / "bundle").as_posix() for p in (run_dir / "bundle").rglob("*") if p.is_file()]
        assert all(not x.endswith(".jsonl") for x in bundle_files)
        assert all(not x.endswith(".kif") for x in bundle_files)
        assert all("logs/" not in x for x in bundle_files)
        assert all("artifacts/" not in x for x in bundle_files)


def test_privacy_scan_strict_fail_on_forbidden_token() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-discord-scan-") as td:
        d = Path(td)
        (d / "ok.txt").write_text("safe\n", encoding="utf-8")
        (d / "leak.txt").write_text("/Users/taso/private\n", encoding="utf-8")
        rc, out, _err = _run(
            [
                sys.executable,
                "scripts/scan_release_privacy.py",
                "--target-dir",
                str(d),
                "--strict",
            ]
        )
        assert rc == 1
        payload = json.loads(out.strip())
        assert payload["status"] == "failed"
        assert payload["text_pattern_hits"]


def test_split_parts_below_limit() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-discord-rel-") as td:
        d = Path(td)
        project = d / "project"
        out = d / "out"
        _seed_min_project(project, nn_size_bytes=3 * 1024 * 1024)
        run_dir = _build_release(project, out, "r3", part_size_mb=1)

        parts = sorted((run_dir / "parts").glob("*.part.*"))
        assert parts
        assert all(p.stat().st_size <= 1024 * 1024 for p in parts)


def test_restore_roundtrip_hash_match() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-discord-rel-") as td:
        d = Path(td)
        project = d / "project"
        out = d / "out"
        _seed_min_project(project, nn_size_bytes=3 * 1024 * 1024)
        run_dir = _build_release(project, out, "r4", part_size_mb=1)

        summary = run_dir / "restore_summary.json"
        rc, _out, err = _run(
            [
                sys.executable,
                "scripts/restore_discord_parts.py",
                "--parts-dir",
                str(run_dir / "parts"),
                "--manifest",
                str(run_dir / "manifest.json"),
                "--summary-out",
                str(summary),
            ]
        )
        assert rc == 0, err
        payload = json.loads(summary.read_text(encoding="utf-8"))
        assert payload["status"] == "ok"
        assert payload["sha256_match"] is True
        extracted = Path(payload["extract_dir"])
        assert (extracted / "launch_taso_swindle_discord.command").exists()


def test_launch_script_points_to_relative_hybrid_path() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-discord-rel-") as td:
        d = Path(td)
        project = d / "project"
        out = d / "out"
        _seed_min_project(project)
        run_dir = _build_release(project, out, "r5", part_size_mb=1)

        launch = (run_dir / "bundle" / "launch_taso_swindle_discord.command").read_text(encoding="utf-8")
        assert 'TASO_SWINDLE_SWINDLE_HYBRID_WEIGHTS_PATH="$SCRIPT_DIR/models/hybrid_weights.json"' in launch
        assert 'TASO_SWINDLE_SWINDLE_MODE="HYBRID"' in launch
        assert 'TASO_SWINDLE_SWINDLE_ENABLE="true"' in launch


if __name__ == "__main__":
    test_release_profile_flavor_with_hybrid()
    test_release_excludes_logs_kifu_jsonl()
    test_privacy_scan_strict_fail_on_forbidden_token()
    test_split_parts_below_limit()
    test_restore_roundtrip_hash_match()
    test_launch_script_points_to_relative_hybrid_path()
    print("ok test_discord_release_bundle")
