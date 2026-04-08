#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _elo_from_score(score: float) -> float:
    s = max(1e-6, min(1.0 - 1e-6, float(score)))
    return -400.0 * math.log10((1.0 / s) - 1.0)


def _safe_band_label(dan_value: float) -> str:
    if dan_value < 1.0:
        return "級位〜初段未満（推定）"
    lo = max(1, int(math.floor(dan_value)))
    hi = max(lo, int(math.ceil(dan_value)))
    if lo == hi:
        return f"アマ{lo}段前後（推定）"
    return f"アマ{lo}〜{hi}段相当（推定）"


@dataclass(frozen=True)
class MatchStats:
    games: int
    wins: int
    losses: int
    draws: int
    score_rate: float
    win_rate: float
    draw_rate: float
    elo_diff: float
    elo_ci95_low: float
    elo_ci95_high: float


def _compute_stats(results: list[float]) -> MatchStats:
    n = len(results)
    if n <= 0:
        return MatchStats(
            games=0,
            wins=0,
            losses=0,
            draws=0,
            score_rate=0.5,
            win_rate=0.0,
            draw_rate=0.0,
            elo_diff=0.0,
            elo_ci95_low=-9999.0,
            elo_ci95_high=9999.0,
        )

    wins = sum(1 for x in results if x >= 0.99)
    losses = sum(1 for x in results if x <= 0.01)
    draws = n - wins - losses
    mean = sum(results) / float(n)

    if n > 1:
        var = sum((x - mean) ** 2 for x in results) / float(n - 1)
    else:
        var = mean * (1.0 - mean)
    se = math.sqrt(max(0.0, var) / float(n))
    lo_score = max(1e-6, mean - 1.96 * se)
    hi_score = min(1.0 - 1e-6, mean + 1.96 * se)

    return MatchStats(
        games=n,
        wins=wins,
        losses=losses,
        draws=draws,
        score_rate=mean,
        win_rate=wins / float(n),
        draw_rate=draws / float(n),
        elo_diff=_elo_from_score(mean),
        elo_ci95_low=_elo_from_score(lo_score),
        elo_ci95_high=_elo_from_score(hi_score),
    )


def _collect_game_results(games_jsonl: Path) -> list[float]:
    out: list[float] = []
    if not games_jsonl.exists():
        return out
    for raw in games_jsonl.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        winner = str(rec.get("winner", "")).strip().lower()
        wrapper_is_black = bool(rec.get("wrapper_is_black"))
        if winner == "draw" or not winner:
            out.append(0.5)
            continue
        if winner == "black":
            out.append(1.0 if wrapper_is_black else 0.0)
            continue
        if winner == "white":
            out.append(0.0 if wrapper_is_black else 1.0)
            continue
        out.append(0.5)
    return out


def _summary_md(
    *,
    run_id: str,
    stats: MatchStats,
    baseline_label: str,
    baseline_dan_anchor: float,
    est_dan: float,
    dan_label: str,
    report_path: Path,
) -> str:
    return "\n".join(
        [
            f"# ELO Calibration: {run_id}",
            "",
            f"- Baseline: `{baseline_label}`",
            f"- Games: `{stats.games}`",
            f"- W/L/D: `{stats.wins}/{stats.losses}/{stats.draws}`",
            f"- Score rate: `{stats.score_rate:.4f}` (win rate `{stats.win_rate:.4f}`, draw rate `{stats.draw_rate:.4f}`)",
            f"- Elo diff vs baseline: `{stats.elo_diff:+.1f}`",
            f"- Elo 95% CI: `[{stats.elo_ci95_low:+.1f}, {stats.elo_ci95_high:+.1f}]`",
            "",
            "## Amateur-dan proxy (rough)",
            f"- Anchor: baseline is treated as `アマ{baseline_dan_anchor:.1f}段`",
            f"- Estimated dan proxy: `{est_dan:.2f}` -> **{dan_label}**",
            "- Note: This dan proxy is rough and depends on the chosen baseline anchor.",
            "",
            f"- Full report: `{report_path}`",
            "",
        ]
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Elo calibration for TASO-SWINDLE vs baseline backend.")
    parser.add_argument("--wrapper-cmd", default="python3 -m taso_swindle.main")
    parser.add_argument("--backend-engine", default="./YaneuraOu")
    parser.add_argument("--backend-eval", default="./eval")
    parser.add_argument("--backend-args", default="")
    parser.add_argument("--backend-options", default="Threads=8;Hash=8192;BookFile=no_book")
    parser.add_argument("--wrapper-options", default="")
    parser.add_argument("--swindle-mode", default="HYBRID", choices=["AUTO", "TACTICAL", "MURKY", "HYBRID"])
    parser.add_argument("--games", type=int, default=300)
    parser.add_argument("--nodes", type=int, default=800)
    parser.add_argument("--movetime-ms", type=int, default=0)
    parser.add_argument("--max-plies", type=int, default=140)
    parser.add_argument("--opening-file", default="artifacts_local/benchmarks/fixed_bench_openings_v1.txt")
    parser.add_argument("--opening-plies", type=int, default=10)
    parser.add_argument("--seed", type=int, default=20260303)
    parser.add_argument("--think-timeout-sec", type=float, default=4.0)
    parser.add_argument("--game-walltime-sec", type=float, default=90.0)
    parser.add_argument("--output-root", default="artifacts_local/elo_calibration")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--disable-resign", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--baseline-label", default="YaneuraOu baseline")
    parser.add_argument("--baseline-dan-anchor", type=float, default=5.0)
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = (root / output_root).resolve()
    run_id = args.run_id.strip() or f"elo-{_ts()}"
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_path = run_dir / "summary.json"
    report_path = run_dir / "elo_report.json"
    md_path = run_dir / "elo_summary.md"

    wrapper_options = args.wrapper_options.strip()
    if not wrapper_options:
        wrapper_options = ";".join(
            [
                "SwindleDryRun=false",
                "SwindleEnable=true",
                f"SwindleMode={args.swindle_mode}",
                "SwindleUseHybridLearnedAdjustment=true",
                "SwindleUsePonderGateLearnedAdjustment=false",
                "SwindleVerboseInfo=false",
                "SwindleEmitInfoStringLevel=0",
                f"SwindleHybridWeightsPath={root / 'models/hybrid_weights.json'}",
            ]
        )

    selfplay_cmd = [
        "python3",
        "scripts/run_selfplay_dataset.py",
        "--games",
        str(int(args.games)),
        "--max-plies",
        str(int(args.max_plies)),
        "--nodes",
        str(int(args.nodes)),
        "--movetime-ms",
        str(int(args.movetime_ms)),
        "--think-timeout-sec",
        f"{float(args.think_timeout_sec):.3f}",
        "--game-walltime-sec",
        f"{float(args.game_walltime_sec):.3f}",
        "--opening-file",
        str(args.opening_file),
        "--opening-plies",
        str(int(args.opening_plies)),
        "--seed",
        str(int(args.seed)),
        "--wrapper-cmd",
        str(args.wrapper_cmd),
        "--backend-engine",
        str(args.backend_engine),
        "--backend-eval",
        str(args.backend_eval),
        "--backend-args",
        str(args.backend_args),
        "--wrapper-options",
        wrapper_options,
        "--backend-options",
        str(args.backend_options),
        "--swindle-mode",
        str(args.swindle_mode),
        "--output-root",
        str(output_root),
        "--run-id",
        run_id,
        "--no-auto-pipeline",
        "--disable-resign" if args.disable_resign else "--no-disable-resign",
    ]

    print(f"[elo] run: {' '.join(shlex.quote(x) for x in selfplay_cmd)}")
    rc = subprocess.run(selfplay_cmd, cwd=str(root), text=True).returncode  # noqa: S603,S607
    if rc != 0:
        _json_dump(
            report_path,
            {
                "run_id": run_id,
                "timestamp": _now_iso(),
                "status": "failed",
                "selfplay_rc": int(rc),
                "selfplay_cmd": selfplay_cmd,
            },
        )
        print(f"[elo] selfplay failed rc={rc}")
        return int(rc)

    if not summary_path.exists():
        _json_dump(
            report_path,
            {
                "run_id": run_id,
                "timestamp": _now_iso(),
                "status": "failed",
                "reason": "summary_missing",
                "expected_summary": str(summary_path),
            },
        )
        print(f"[elo] missing summary: {summary_path}")
        return 2

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    games_jsonl = Path(str(summary.get("games_jsonl", ""))).expanduser()
    if not games_jsonl.is_absolute():
        games_jsonl = (root / games_jsonl).resolve()
    results = _collect_game_results(games_jsonl)
    stats = _compute_stats(results)

    # Rough dan proxy: +200 Elo ~= +1 dan around anchor.
    est_dan = float(args.baseline_dan_anchor) + (stats.elo_diff / 200.0)
    dan_label = _safe_band_label(est_dan)

    report = {
        "run_id": run_id,
        "timestamp": _now_iso(),
        "status": "ok",
        "baseline_label": args.baseline_label,
        "baseline_dan_anchor": float(args.baseline_dan_anchor),
        "selfplay_summary_path": str(summary_path),
        "games_jsonl": str(games_jsonl),
        "metrics": {
            "games": stats.games,
            "wins": stats.wins,
            "losses": stats.losses,
            "draws": stats.draws,
            "score_rate": stats.score_rate,
            "win_rate": stats.win_rate,
            "draw_rate": stats.draw_rate,
            "elo_diff_vs_baseline": stats.elo_diff,
            "elo_ci95_low": stats.elo_ci95_low,
            "elo_ci95_high": stats.elo_ci95_high,
        },
        "amateur_dan_proxy": {
            "estimated_dan": est_dan,
            "label": dan_label,
            "formula": "baseline_dan_anchor + elo_diff/200",
            "caveat": "rough_proxy_not_official_rating",
        },
        "config": {
            "wrapper_cmd": args.wrapper_cmd,
            "backend_engine": args.backend_engine,
            "backend_eval": args.backend_eval,
            "backend_args": args.backend_args,
            "backend_options": args.backend_options,
            "wrapper_options": wrapper_options,
            "games": int(args.games),
            "nodes": int(args.nodes),
            "movetime_ms": int(args.movetime_ms),
            "max_plies": int(args.max_plies),
            "opening_file": args.opening_file,
            "opening_plies": int(args.opening_plies),
            "seed": int(args.seed),
            "think_timeout_sec": float(args.think_timeout_sec),
            "game_walltime_sec": float(args.game_walltime_sec),
        },
    }
    _json_dump(report_path, report)
    md_path.write_text(
        _summary_md(
            run_id=run_id,
            stats=stats,
            baseline_label=str(args.baseline_label),
            baseline_dan_anchor=float(args.baseline_dan_anchor),
            est_dan=est_dan,
            dan_label=dan_label,
            report_path=report_path,
        ),
        encoding="utf-8",
    )

    print(f"[elo] report: {report_path}")
    print(f"[elo] summary: {md_path}")
    print(
        f"[elo] result: score={stats.score_rate:.4f}, elo={stats.elo_diff:+.1f}, "
        f"ci95=[{stats.elo_ci95_low:+.1f},{stats.elo_ci95_high:+.1f}], dan_proxy={dan_label}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

