#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_policies import anonymize_learning_sample


def _ts_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            if raw.strip():
                n += 1
    return n


def _count_by_source(path: Path, *, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not path.exists():
        return counts
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            source = str(rec.get(key, "") or "").strip()
            if not source:
                continue
            counts[source] = counts.get(source, 0) + 1
    return counts


def _run_cmd(cmd: list[str], *, cwd: Path, errors: list[str], warnings: list[str]) -> bool:
    done = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)  # noqa: S603,S607
    if done.returncode != 0:
        errors.append(f"cmd_failed:{' '.join(cmd)}:rc={done.returncode}")
        if done.stderr:
            warnings.append(done.stderr.strip()[:400])
        return False
    return True


def _artifact_valid(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    if path.suffix == ".jsonl":
        return _count_jsonl(path) > 0
    if path.suffix == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return isinstance(payload, (dict, list))
        except Exception:
            return False
    return path.stat().st_size > 0


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _hash_dir(path: Path) -> str:
    h = hashlib.sha256()
    files = sorted([p for p in path.rglob("*") if p.is_file()])
    for p in files:
        rel = str(p.relative_to(path)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(_hash_file(p).encode("utf-8"))
    return h.hexdigest()


def _path_fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "kind": "missing", "hash": "missing"}
    if path.is_file():
        return {"path": str(path), "kind": "file", "hash": _hash_file(path)}
    if path.is_dir():
        return {"path": str(path), "kind": "dir", "hash": _hash_dir(path)}
    return {"path": str(path), "kind": "other", "hash": "unsupported"}


def _stage_hash(stage: str, cmd: list[str], inputs: list[Path], extra: dict[str, Any] | None = None) -> str:
    payload = {
        "stage": stage,
        "cmd": cmd,
        "inputs": [_path_fingerprint(p) for p in inputs],
        "extra": extra or {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(raw)


def _load_stage_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    stages = data.get("stages")
    return stages if isinstance(stages, dict) else {}


def _save_stage_manifest(path: Path, stages: dict[str, Any]) -> None:
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stages": stages,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_logs(inputs: list[str], dst: Path, warnings: list[str]) -> tuple[int, list[dict[str, Any]]]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    source_stats: list[dict[str, Any]] = []
    with dst.open("w", encoding="utf-8") as out:
        for raw in inputs:
            path = Path(raw)
            rec_count = 0
            if not path.exists():
                warnings.append(f"log_missing:{path}")
                source_stats.append(
                    {
                        "path": str(path),
                        "record_count": 0,
                        "labeled_count": 0,
                        "training_count": 0,
                    }
                )
                continue
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    row = line.strip()
                    if not row:
                        continue
                    rec_count += 1
                    total += 1
                    try:
                        rec = json.loads(row)
                    except Exception:
                        out.write(row + "\n")
                        continue
                    if isinstance(rec, dict):
                        rec.setdefault("_source_log_path", str(path))
                        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    else:
                        out.write(row + "\n")
            source_stats.append(
                {
                    "path": str(path),
                    "record_count": rec_count,
                    "labeled_count": 0,
                    "training_count": 0,
                }
            )
    return total, source_stats


def _update_source_counts(source_stats: list[dict[str, Any]], values: dict[str, int], *, key: str) -> None:
    index: dict[str, dict[str, Any]] = {
        str(item.get("path", "")): item
        for item in source_stats
        if isinstance(item, dict)
    }
    for source, count in values.items():
        row = index.get(source)
        if row is None:
            row = {
                "path": source,
                "record_count": 0,
                "labeled_count": 0,
                "training_count": 0,
            }
            source_stats.append(row)
            index[source] = row
        row[key] = int(count)


def _init_source_stats(logs: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(Path(raw)),
            "record_count": 0,
            "labeled_count": 0,
            "training_count": 0,
        }
        for raw in logs
    ]


def _source_token(path_value: str) -> str:
    token = anonymize_learning_sample(
        {"source_log_path": path_value},
        enabled=True,
        salt="",
    ).get("source_log_path")
    return str(token or "")


def _force_stage_set(values: list[str]) -> set[str]:
    out: set[str] = set()
    for raw in values:
        for part in str(raw).split(","):
            stage = part.strip()
            if stage:
                out.add(stage)
    return out


def _select_run_dir(output_root: Path, resume: bool) -> tuple[str, Path, bool]:
    if resume and output_root.exists():
        runs = [p for p in output_root.iterdir() if p.is_dir()]
        runs.sort(key=lambda p: p.name, reverse=True)
        if runs:
            latest = runs[0]
            return latest.name, latest, True
    run_id = _ts_id()
    return run_id, (output_root / run_id), False


def _find_previous_weight(artifacts_root: Path, current_run: str, filename: str) -> Path | None:
    if not artifacts_root.exists():
        return None
    runs = [p for p in artifacts_root.iterdir() if p.is_dir() and p.name != current_run]
    runs.sort(key=lambda p: p.name, reverse=True)
    for run in runs:
        candidate = run / "weights" / filename
        if _artifact_valid(candidate):
            return candidate
    return None


def _find_previous_run_dir(artifacts_root: Path, current_run: str) -> Path | None:
    if not artifacts_root.exists():
        return None
    runs = [p for p in artifacts_root.iterdir() if p.is_dir() and p.name != current_run]
    runs.sort(key=lambda p: p.name, reverse=True)
    return runs[0] if runs else None


def _pick_eval_log_for_run(run_dir: Path) -> Path | None:
    candidates = [
        run_dir / "training_labels" / "training_labels.jsonl",
        run_dir / "labeled" / "from_csa.labeled.jsonl",
        run_dir / "labeled" / "from_kif.labeled.jsonl",
        run_dir / "raw" / "merged_raw.jsonl",
    ]
    for p in candidates:
        if _artifact_valid(p):
            return p
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TASO-SWINDLE offline learning pipeline (semi-automated).")
    parser.add_argument("--logs", nargs="+", required=True, help="input raw log JSONL(s)")
    parser.add_argument("--kif-dir", default="", help="optional KIF directory (multiple files)")
    parser.add_argument("--csa-dir", default="", help="optional CSA directory (multiple files)")
    parser.add_argument("--output-root", default="artifacts/learning_runs", help="artifact root")
    parser.add_argument("--train-ponder", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--train-hybrid", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--skip-fill-outcomes", action="store_true")
    parser.add_argument("--skip-quality-report", action="store_true")
    parser.add_argument("--prefer-labeled-outcome", action="store_true")
    parser.add_argument("--min-outcome-confidence", type=float, default=0.0)
    parser.add_argument("--min-outcome-match-confidence", type=float, default=0.0)
    parser.add_argument("--ponder-label-mode", default="runtime_first", choices=["heuristic_only", "runtime_first", "mixed"])
    parser.add_argument("--min-ponder-label-confidence", type=float, default=0.0)
    parser.add_argument("--resume", action="store_true", help="reuse existing stage artifacts in run dir when valid")
    parser.add_argument("--retry", type=int, default=0, help="retry count for failed external stages")
    parser.add_argument("--force-stage", action="append", default=[], help="force re-run stage(s) even with --resume")
    parser.add_argument("--compare-prev-weights", action="store_true", help="compare generated weights against previous run weights")
    parser.add_argument(
        "--compliance-personal-use-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="compliance flag: training artifacts are for personal use only",
    )
    args = parser.parse_args()

    retry_max = max(0, int(args.retry))
    force_stages = _force_stage_set(args.force_stage)

    started = time.time()
    output_root = Path(args.output_root)
    run_id, run_dir, resume_used_actual = _select_run_dir(output_root, bool(args.resume))
    raw_dir = run_dir / "raw"
    labeled_dir = run_dir / "labeled"
    training_dir = run_dir / "training_labels"
    weights_dir = run_dir / "weights"
    reports_dir = run_dir / "reports"
    for d in (raw_dir, labeled_dir, training_dir, weights_dir, reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    stage_manifest_path = run_dir / "stage_manifest.json"
    stage_manifest_prev = _load_stage_manifest(stage_manifest_path)
    stage_manifest_next = dict(stage_manifest_prev)

    errors: list[str] = []
    warnings: list[str] = []
    partial_outputs: list[dict[str, str]] = []
    failed_stage = ""
    retry_count_by_stage: dict[str, int] = {}
    skipped_stages: list[str] = []
    executed_stages: list[str] = []
    compare_reports: list[dict[str, Any]] = []
    compare_skips: list[str] = []
    stage_hash_mismatch_stages: list[str] = []

    partial_seen: set[tuple[str, str]] = set()

    def add_partial(stage: str, path: Path) -> None:
        key = (stage, str(path))
        if key in partial_seen:
            return
        partial_seen.add(key)
        partial_outputs.append({"stage": stage, "path": str(path)})

    def mark_stage(stage: str, stage_hash: str, outputs: list[Path], cmd: list[str], extra: dict[str, Any] | None = None) -> None:
        stage_manifest_next[stage] = {
            "hash": stage_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "outputs": [str(p) for p in outputs],
            "cmd": cmd,
            "extra": extra or {},
        }

    def should_skip(stage: str, outputs: list[Path], stage_hash: str) -> bool:
        if not args.resume:
            return False
        if stage in force_stages:
            return False
        if not outputs:
            return False
        if not all(_artifact_valid(p) for p in outputs):
            return False

        prev = stage_manifest_prev.get(stage)
        prev_hash = str(prev.get("hash", "")) if isinstance(prev, dict) else ""
        if not prev_hash:
            return False
        if prev_hash != stage_hash:
            if stage not in stage_hash_mismatch_stages:
                stage_hash_mismatch_stages.append(stage)
            return False
        return True

    def run_external_stage(
        stage: str,
        cmd: list[str],
        outputs: list[Path],
        stage_inputs: list[Path],
        stage_extra: dict[str, Any] | None = None,
    ) -> bool:
        nonlocal failed_stage
        stage_hash = _stage_hash(stage, cmd, stage_inputs, stage_extra)
        if should_skip(stage, outputs, stage_hash):
            skipped_stages.append(stage)
            for p in outputs:
                add_partial(stage, p)
            return True

        executed_stages.append(stage)
        attempts = 0
        while True:
            ok = _run_cmd(cmd, cwd=Path.cwd(), errors=errors, warnings=warnings)
            if ok:
                for p in outputs:
                    if p.exists():
                        add_partial(stage, p)
                mark_stage(stage, stage_hash, outputs, cmd, stage_extra)
                return True
            if attempts >= retry_max:
                if not failed_stage:
                    failed_stage = stage
                return False
            attempts += 1
            retry_count_by_stage[stage] = attempts
            warnings.append(f"retry:{stage}:{attempts}/{retry_max}")

    merged_raw = raw_dir / "merged_raw.jsonl"
    input_logs_count = len(args.logs)
    merge_cmd = ["merge_logs", *args.logs]
    merge_hash = _stage_hash("merge_logs", merge_cmd, [Path(x) for x in args.logs], {"input_logs_count": input_logs_count})

    if should_skip("merge_logs", [merged_raw], merge_hash):
        skipped_stages.append("merge_logs")
        records_raw = _count_jsonl(merged_raw)
        input_sources = _init_source_stats(args.logs)
        _update_source_counts(input_sources, _count_by_source(merged_raw, key="_source_log_path"), key="record_count")
        add_partial("merge_logs", merged_raw)
    else:
        executed_stages.append("merge_logs")
        records_raw, input_sources = _merge_logs(args.logs, merged_raw, warnings)
        if merged_raw.exists():
            add_partial("merge_logs", merged_raw)
            mark_stage("merge_logs", merge_hash, [merged_raw], merge_cmd, {"input_logs_count": input_logs_count})

    cur_input = merged_raw
    records_labeled = records_raw
    if not args.skip_fill_outcomes:
        raw_kif_dir = (args.kif_dir or "").strip()
        if raw_kif_dir:
            out_kif = labeled_dir / "from_kif.labeled.jsonl"
            ok = run_external_stage(
                "fill_kif",
                [
                    "python3",
                    "scripts/fill_outcomes_from_kif.py",
                    "--input",
                    str(cur_input),
                    "--kif-dir",
                    raw_kif_dir,
                    "--output",
                    str(out_kif),
                ],
                [out_kif],
                [cur_input, Path(raw_kif_dir)],
                {"kif_dir": raw_kif_dir},
            )
            if ok:
                cur_input = out_kif

        raw_csa_dir = (args.csa_dir or "").strip()
        if raw_csa_dir:
            out_csa = labeled_dir / "from_csa.labeled.jsonl"
            ok = run_external_stage(
                "fill_csa",
                [
                    "python3",
                    "scripts/fill_outcomes_from_csa.py",
                    "--input",
                    str(cur_input),
                    "--csa-dir",
                    raw_csa_dir,
                    "--output",
                    str(out_csa),
                ],
                [out_csa],
                [cur_input, Path(raw_csa_dir)],
                {"csa_dir": raw_csa_dir},
            )
            if ok:
                cur_input = out_csa

        records_labeled = _count_jsonl(cur_input)

    quality_report_path = reports_dir / "quality_report.json"
    quality_report_text_path = reports_dir / "quality_report.txt"
    if not args.skip_quality_report:
        run_external_stage(
            "quality_report",
            [
                "python3",
                "scripts/report_learning_data_quality.py",
                "--input",
                str(cur_input),
                "--output-json",
                str(quality_report_path),
                "--output-text",
                str(quality_report_text_path),
                "--min-ponder-label-confidence",
                str(args.min_ponder_label_confidence),
                "--min-outcome-confidence",
                str(args.min_outcome_confidence),
            ],
            [quality_report_path, quality_report_text_path],
            [cur_input],
            {
                "min_ponder_label_confidence": args.min_ponder_label_confidence,
                "min_outcome_confidence": args.min_outcome_confidence,
            },
        )

    training_labels_path = training_dir / "training_labels.jsonl"
    build_ok = run_external_stage(
        "build_labels",
        [
            "python3",
            "scripts/build_training_labels.py",
            "--input",
            str(cur_input),
            "--output",
            str(training_labels_path),
            "--label-mode",
            "mixed",
            "--ponder-label-mode",
            str(args.ponder_label_mode),
            "--min-ponder-label-confidence",
            str(args.min_ponder_label_confidence),
            "--min-outcome-confidence",
            str(args.min_outcome_confidence),
            "--min-outcome-match-confidence",
            str(args.min_outcome_match_confidence),
            *(["--prefer-labeled-outcome"] if args.prefer_labeled_outcome else []),
        ],
        [training_labels_path],
        [cur_input],
        {
            "ponder_label_mode": args.ponder_label_mode,
            "min_ponder_label_confidence": args.min_ponder_label_confidence,
            "min_outcome_confidence": args.min_outcome_confidence,
            "min_outcome_match_confidence": args.min_outcome_match_confidence,
            "prefer_labeled_outcome": bool(args.prefer_labeled_outcome),
        },
    )
    records_training = _count_jsonl(training_labels_path) if build_ok else 0

    ponder_weights_path = weights_dir / "ponder_gate_weights.json"
    hybrid_weights_path = weights_dir / "hybrid_weights.json"

    ponder_training_run = False
    if args.train_ponder and build_ok:
        ponder_training_run = run_external_stage(
            "train_ponder",
            [
                "python3",
                "scripts/train_ponder_gate.py",
                "--input",
                str(training_labels_path),
                "--output",
                str(ponder_weights_path),
                "--label-mode",
                "mixed",
            ],
            [ponder_weights_path],
            [training_labels_path],
            {"label_mode": "mixed"},
        )
        if not ponder_training_run:
            run_external_stage(
                "export_weights",
                [
                    "python3",
                    "scripts/export_ponder_gate_weights.py",
                    "--output",
                    str(ponder_weights_path),
                ],
                [ponder_weights_path],
                [],
                {"kind": "ponder"},
            )

    hybrid_training_run = False
    if args.train_hybrid and build_ok:
        hybrid_training_run = run_external_stage(
            "train_hybrid",
            [
                "python3",
                "scripts/train_hybrid_confidence.py",
                "--input",
                str(training_labels_path),
                "--output",
                str(hybrid_weights_path),
                "--label-mode",
                "mixed",
            ],
            [hybrid_weights_path],
            [training_labels_path],
            {"label_mode": "mixed"},
        )
        if not hybrid_training_run:
            run_external_stage(
                "export_weights",
                [
                    "python3",
                    "scripts/export_hybrid_weights.py",
                    "--output",
                    str(hybrid_weights_path),
                ],
                [hybrid_weights_path],
                [],
                {"kind": "hybrid"},
            )

    if args.compare_prev_weights:
        artifacts_root = output_root
        prev_run_dir = _find_previous_run_dir(artifacts_root, run_id)
        prev_eval = _pick_eval_log_for_run(prev_run_dir) if prev_run_dir is not None else None
        cur_eval = training_labels_path if _artifact_valid(training_labels_path) else (cur_input if _artifact_valid(cur_input) else None)

        if ponder_weights_path.exists():
            prev_ponder = _find_previous_weight(artifacts_root, run_id, "ponder_gate_weights.json")
            if prev_ponder is None:
                compare_skips.append("ponder:no_previous_weight")
            else:
                out_json = reports_dir / "weights_ab_ponder.json"
                out_md = reports_dir / "weights_ab_ponder.md"
                cmd = [
                    "python3",
                    "scripts/report_weights_ab.py",
                    "--a",
                    str(prev_ponder),
                    "--b",
                    str(ponder_weights_path),
                    "--type",
                    "ponder",
                    "--out",
                    str(out_json),
                    "--md-out",
                    str(out_md),
                ]
                stage_inputs = [prev_ponder, ponder_weights_path]
                if prev_eval is not None and cur_eval is not None:
                    cmd.extend(["--eval-log-a", str(prev_eval), "--eval-log-b", str(cur_eval)])
                    stage_inputs.extend([prev_eval, cur_eval])
                ok = run_external_stage(
                    "compare_weights_ponder",
                    cmd,
                    [out_json, out_md],
                    stage_inputs,
                    {"type": "ponder"},
                )
                compare_reports.append(
                    {
                        "type": "ponder",
                        "status": "ok" if ok else "error",
                        "a": str(prev_ponder),
                        "b": str(ponder_weights_path),
                        "json": str(out_json) if out_json.exists() else "",
                        "md": str(out_md) if out_md.exists() else "",
                        "eval_log_a": str(prev_eval) if prev_eval else "",
                        "eval_log_b": str(cur_eval) if cur_eval else "",
                    }
                )
        else:
            compare_skips.append("ponder:no_new_weight")

        if hybrid_weights_path.exists():
            prev_hybrid = _find_previous_weight(artifacts_root, run_id, "hybrid_weights.json")
            if prev_hybrid is None:
                compare_skips.append("hybrid:no_previous_weight")
            else:
                out_json = reports_dir / "weights_ab_hybrid.json"
                out_md = reports_dir / "weights_ab_hybrid.md"
                cmd = [
                    "python3",
                    "scripts/report_weights_ab.py",
                    "--a",
                    str(prev_hybrid),
                    "--b",
                    str(hybrid_weights_path),
                    "--type",
                    "hybrid",
                    "--out",
                    str(out_json),
                    "--md-out",
                    str(out_md),
                ]
                stage_inputs = [prev_hybrid, hybrid_weights_path]
                if prev_eval is not None and cur_eval is not None:
                    cmd.extend(["--eval-log-a", str(prev_eval), "--eval-log-b", str(cur_eval)])
                    stage_inputs.extend([prev_eval, cur_eval])
                ok = run_external_stage(
                    "compare_weights_hybrid",
                    cmd,
                    [out_json, out_md],
                    stage_inputs,
                    {"type": "hybrid"},
                )
                compare_reports.append(
                    {
                        "type": "hybrid",
                        "status": "ok" if ok else "error",
                        "a": str(prev_hybrid),
                        "b": str(hybrid_weights_path),
                        "json": str(out_json) if out_json.exists() else "",
                        "md": str(out_md) if out_md.exists() else "",
                        "eval_log_a": str(prev_eval) if prev_eval else "",
                        "eval_log_b": str(cur_eval) if cur_eval else "",
                    }
                )
        else:
            compare_skips.append("hybrid:no_new_weight")

    labeled_counts = _count_by_source(cur_input, key="_source_log_path")
    _update_source_counts(input_sources, labeled_counts, key="labeled_count")
    if build_ok:
        training_counts_raw = _count_by_source(training_labels_path, key="source_log_path")
        if bool(args.compliance_personal_use_only):
            token_to_path = { _source_token(str(item.get("path", ""))): str(item.get("path", "")) for item in input_sources }
            training_counts: dict[str, int] = {}
            for key, value in training_counts_raw.items():
                mapped = token_to_path.get(str(key), str(key))
                training_counts[mapped] = training_counts.get(mapped, 0) + int(value)
        else:
            training_counts = training_counts_raw
    else:
        training_counts = {}
    _update_source_counts(input_sources, training_counts, key="training_count")

    _save_stage_manifest(stage_manifest_path, stage_manifest_next)

    finished = time.time()
    summary = {
        "run_id": run_id,
        "started_at": datetime.fromtimestamp(started, tz=timezone.utc).isoformat(timespec="seconds"),
        "finished_at": datetime.fromtimestamp(finished, tz=timezone.utc).isoformat(timespec="seconds"),
        "duration_sec": round(max(0.0, finished - started), 3),
        "input_logs_count": input_logs_count,
        "records_raw": records_raw,
        "records_labeled": records_labeled,
        "records_training": records_training,
        "ponder_training_run": bool(ponder_training_run),
        "hybrid_training_run": bool(hybrid_training_run),
        "ponder_weights_path": str(ponder_weights_path) if ponder_weights_path.exists() else "",
        "hybrid_weights_path": str(hybrid_weights_path) if hybrid_weights_path.exists() else "",
        "quality_report_path": str(quality_report_path) if quality_report_path.exists() else "",
        "errors": errors,
        "warnings": warnings,
        "status": "success" if not errors else ("partial" if build_ok else "failed"),
        "failed_stage": failed_stage,
        "partial_outputs": partial_outputs,
        "partial_output_paths": [str(item.get("path", "")) for item in partial_outputs],
        "input_sources": input_sources,
        "resume_used": bool(resume_used_actual),
        "retry_count_by_stage": retry_count_by_stage,
        "skipped_stages": skipped_stages,
        "executed_stages": executed_stages,
        "stage_hash_mismatch_stages": stage_hash_mismatch_stages,
        "stage_manifest_path": str(stage_manifest_path),
        "compare_prev_weights": bool(args.compare_prev_weights),
        "compare_reports": compare_reports,
        "compare_prev_weights_skipped": compare_skips,
        "compliance_mode": "personal_use_only" if bool(args.compliance_personal_use_only) else "unspecified",
    }
    summary_path = run_dir / "summary.json"
    with summary_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))

    if not errors:
        return 0
    return 1 if build_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
