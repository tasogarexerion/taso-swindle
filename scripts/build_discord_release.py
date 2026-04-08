#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scan_release_privacy import run_scan
except Exception:  # pragma: no cover - import path fallback for test/module mode
    from scripts.scan_release_privacy import run_scan


PROFILE_FLAVOR_WITH_HYBRID = "flavor_with_hybrid"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _ignore_copy(_src: str, names: list[str]) -> list[str]:
    ignored: list[str] = []
    for name in names:
        if name == "__pycache__" or name.endswith(".pyc") or name == ".DS_Store":
            ignored.append(name)
    return ignored


def _write_launch_script(path: Path) -> None:
    body = """#!/bin/zsh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export TASO_SWINDLE_BACKEND_ENGINE="$SCRIPT_DIR/YaneuraOu"
export TASO_SWINDLE_BACKEND_ARGS=""
export TASO_SWINDLE_BACKEND_OPTION_PASSTHROUGH="Threads=4;Hash=2048;EvalDir=$SCRIPT_DIR/eval;BookFile=no_book"
export TASO_SWINDLE_SWINDLE_ENABLE="true"
export TASO_SWINDLE_SWINDLE_MODE="HYBRID"
export TASO_SWINDLE_SWINDLE_USE_HYBRID_LEARNED_ADJUSTMENT="true"
export TASO_SWINDLE_SWINDLE_HYBRID_WEIGHTS_PATH="$SCRIPT_DIR/models/hybrid_weights.json"
export TASO_SWINDLE_SWINDLE_USE_PONDER_GATE_LEARNED_ADJUSTMENT="false"
export TASO_SWINDLE_SWINDLE_LOG_ENABLE="false"
export TASO_SWINDLE_SWINDLE_VERBOSE_INFO="false"
export TASO_SWINDLE_SWINDLE_EMIT_INFO_STRING_LEVEL="0"

exec python3 -m taso_swindle.main "$@"
"""
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _write_readme(path: Path) -> None:
    text = """TASO-SWINDLE 配布版 使い方

1. 必要条件
- Python 3.11 以上
- macOS で実行（.command ファイルを利用）

2. 起動
- launch_taso_swindle_discord.command を実行
- 将棋GUIでエンジン登録時はこの .command を指定

3. 主な設定
- 逆転モードはON（SwindleEnable=true）
- 学習補正は hybrid_weights.json を利用
- 追加ログは最小化（配布版ではログ出力抑制）

4. 注意
- この配布版には学習ログや棋譜は含まれていません
- 受信側で eval/nn.bin を削除しないでください
"""
    path.write_text(text, encoding="utf-8")


def _write_restore_guide(path: Path, zip_name: str) -> None:
    text = f"""分割ファイル復元手順

1. 同じフォルダに part ファイルを置く
2. ターミナルで以下を実行

cat {zip_name}.part.* > {zip_name}
shasum -a 256 {zip_name}
unzip {zip_name}

3. 展開後 launch_taso_swindle_discord.command で起動
"""
    path.write_text(text, encoding="utf-8")


def _write_license_notice(path: Path) -> None:
    text = """LICENSE NOTICE

This bundle contains executable/program files and evaluation data.
Use within personal/private scope and comply with each upstream license.
"""
    path.write_text(text, encoding="utf-8")


@dataclass(frozen=True)
class BundlePaths:
    run_dir: Path
    bundle_dir: Path
    zip_dir: Path
    parts_dir: Path
    manifest_json: Path
    privacy_json: Path
    summary_md: Path
    zip_path: Path


def _prepare_paths(output_root: Path, run_id: str, zip_name: str) -> BundlePaths:
    run_dir = output_root / run_id
    bundle_dir = run_dir / "bundle"
    zip_dir = run_dir / "zip"
    parts_dir = run_dir / "parts"
    manifest_json = run_dir / "manifest.json"
    privacy_json = run_dir / "privacy_audit.json"
    summary_md = run_dir / "SUMMARY.md"
    zip_path = zip_dir / zip_name
    return BundlePaths(run_dir, bundle_dir, zip_dir, parts_dir, manifest_json, privacy_json, summary_md, zip_path)


def _copy_required(project_root: Path, bundle_dir: Path, zip_name: str) -> None:
    required = [
        project_root / "YaneuraOu",
        project_root / "eval" / "nn.bin",
        project_root / "models" / "hybrid_weights.json",
    ]
    for p in required:
        if not p.exists():
            raise SystemExit(f"required file missing: {p}")

    (bundle_dir / "eval").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "models").mkdir(parents=True, exist_ok=True)
    shutil.copy2(project_root / "YaneuraOu", bundle_dir / "YaneuraOu")
    shutil.copy2(project_root / "eval" / "nn.bin", bundle_dir / "eval" / "nn.bin")
    shutil.copy2(project_root / "models" / "hybrid_weights.json", bundle_dir / "models" / "hybrid_weights.json")

    src_pkg = project_root / "taso_swindle"
    dst_pkg = bundle_dir / "taso_swindle"
    if dst_pkg.exists():
        shutil.rmtree(dst_pkg)
    shutil.copytree(src_pkg, dst_pkg, ignore=_ignore_copy)

    _write_launch_script(bundle_dir / "launch_taso_swindle_discord.command")
    _write_readme(bundle_dir / "README_PLAY_JA.txt")
    _write_restore_guide(bundle_dir / "RESTORE_FROM_PARTS_JA.txt", zip_name)
    _write_license_notice(bundle_dir / "LICENSE_NOTICE.txt")


def _iter_files(root: Path) -> list[Path]:
    return sorted([p for p in root.rglob("*") if p.is_file()])


def _write_manifest_sha256(bundle_dir: Path) -> Path:
    manifest = bundle_dir / "MANIFEST_SHA256.txt"
    lines: list[str] = []
    for file in _iter_files(bundle_dir):
        rel = file.relative_to(bundle_dir)
        if rel.as_posix() == "MANIFEST_SHA256.txt":
            continue
        lines.append(f"{_sha256(file)}  {rel.as_posix()}")
    manifest.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return manifest


def _build_zip(bundle_dir: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in _iter_files(bundle_dir):
            arc = f.relative_to(bundle_dir).as_posix()
            zf.write(f, arc)


def _split_file(src: Path, parts_dir: Path, part_size_mb: int) -> list[Path]:
    parts_dir.mkdir(parents=True, exist_ok=True)
    part_size = max(1, int(part_size_mb)) * 1024 * 1024
    out: list[Path] = []
    with src.open("rb") as fh:
        idx = 0
        while True:
            chunk = fh.read(part_size)
            if not chunk:
                break
            part = parts_dir / f"{src.name}.part.{idx:03d}"
            part.write_bytes(chunk)
            out.append(part)
            idx += 1
    return out


def _summary_markdown(*, run_id: str, manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Discord Release Summary: {run_id}",
            "",
            f"- profile: `{manifest.get('profile')}`",
            f"- created_at: `{manifest.get('created_at')}`",
            f"- strict_scan: `{manifest.get('strict_scan')}`",
            f"- privacy_ok: `{manifest.get('privacy_ok')}`",
            f"- zip_path: `{manifest.get('zip_path')}`",
            f"- zip_size_bytes: `{manifest.get('zip_size_bytes')}`",
            f"- zip_sha256: `{manifest.get('zip_sha256')}`",
            f"- parts: `{len(manifest.get('parts', []))}`",
            "",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Discord-friendly TASO-SWINDLE release bundle.")
    parser.add_argument("--profile", default=PROFILE_FLAVOR_WITH_HYBRID, choices=[PROFILE_FLAVOR_WITH_HYBRID])
    parser.add_argument("--output-root", default="artifacts_local/discord_release")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--part-size-mb", type=int, default=29)
    parser.add_argument("--zip-name", default="taso_swindle_discord_bundle.zip")
    parser.add_argument("--strict-scan", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--keep-zip", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--no-split", action="store_true", help="do not generate split part files")
    parser.add_argument("--project-root", default="", help="optional project root for tests")
    args = parser.parse_args()

    if str(args.project_root).strip():
        project_root = Path(args.project_root).expanduser().resolve()
    else:
        project_root = Path(__file__).resolve().parent.parent

    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = (project_root / output_root).resolve()
    run_id = args.run_id.strip() or f"discord-release-{_ts()}"

    paths = _prepare_paths(output_root, run_id, args.zip_name)
    paths.bundle_dir.mkdir(parents=True, exist_ok=True)
    paths.zip_dir.mkdir(parents=True, exist_ok=True)

    _copy_required(project_root, paths.bundle_dir, args.zip_name)
    _write_manifest_sha256(paths.bundle_dir)

    scan_result = run_scan(target_dir=paths.bundle_dir)
    _json_dump(paths.privacy_json, scan_result.to_dict())
    if bool(args.strict_scan) and not scan_result.ok:
        print(json.dumps({"status": "failed", "reason": "privacy_scan_failed", "privacy_audit": str(paths.privacy_json)}))
        return 1

    _build_zip(paths.bundle_dir, paths.zip_path)
    parts: list[Path] = []
    if not bool(args.no_split):
        parts = _split_file(paths.zip_path, paths.parts_dir, int(args.part_size_mb))

    if not bool(args.keep_zip) and paths.zip_path.exists():
        paths.zip_path.unlink()

    bundle_files = []
    for f in _iter_files(paths.bundle_dir):
        bundle_files.append(
            {
                "path": str(f.relative_to(paths.bundle_dir)),
                "size_bytes": int(f.stat().st_size),
                "sha256": _sha256(f),
            }
        )
    manifest = {
        "run_id": run_id,
        "profile": args.profile,
        "created_at": _now_iso(),
        "project_root": str(project_root),
        "output_root": str(output_root),
        "bundle_dir": str(paths.bundle_dir),
        "zip_path": str(paths.zip_path),
        "zip_size_bytes": int(paths.zip_path.stat().st_size) if paths.zip_path.exists() else 0,
        "zip_sha256": _sha256(paths.zip_path) if paths.zip_path.exists() else "",
        "part_size_mb": int(args.part_size_mb),
        "parts": [{"path": str(p), "size_bytes": int(p.stat().st_size)} for p in parts],
        "strict_scan": bool(args.strict_scan),
        "privacy_ok": bool(scan_result.ok),
        "privacy_audit_path": str(paths.privacy_json),
        "bundle_files": bundle_files,
    }
    _json_dump(paths.manifest_json, manifest)
    paths.summary_md.write_text(_summary_markdown(run_id=run_id, manifest=manifest), encoding="utf-8")

    print(json.dumps({"status": "ok", "run_id": run_id, "manifest": str(paths.manifest_json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
