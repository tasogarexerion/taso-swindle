#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import random
import shlex
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from taso_swindle.usi_messages import is_special_bestmove, is_usi_move_token


DEFAULT_OPENINGS: tuple[tuple[str, ...], ...] = (
    ("7g7f", "3c3d", "2g2f", "8c8d"),
    ("2g2f", "8c8d", "7g7f", "3c3d"),
    ("7g7f", "8c8d", "2g2f", "3c3d"),
    ("2g2f", "3c3d", "7g7f", "8c8d"),
    ("7g7f", "3c3d", "6g6f", "4c4d"),
    ("2g2f", "8c8d", "2f2e", "8d8e"),
    ("7g7f", "8c8d", "7f7e", "8d8e"),
    ("2g2f", "3c3d", "2f2e", "3d3e"),
)


@dataclass(frozen=True)
class GoResult:
    bestmove: str
    ponder: str
    raw_bestmove: str
    info_lines: list[str]
    timed_out: bool
    engine_dead: bool


class ProcIO:
    def __init__(self, cmd: list[str], cwd: Path) -> None:
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def _read_loop(self) -> None:
        out = self.proc.stdout
        if out is None:
            return
        for raw in out:
            self._queue.put(raw.rstrip("\r\n"))

    def send(self, line: str) -> None:
        sin = self.proc.stdin
        if sin is None:
            raise RuntimeError("stdin is not available")
        sin.write(line + "\n")
        sin.flush()

    def read_until(self, pred: Callable[[str], bool], timeout_sec: float) -> tuple[list[str], bool]:
        deadline = time.time() + timeout_sec
        out: list[str] = []
        while time.time() < deadline:
            try:
                line = self._queue.get(timeout=0.05)
            except queue.Empty:
                if not self.alive:
                    break
                continue
            out.append(line)
            if pred(line):
                return out, True
        return out, False

    def close(self) -> None:
        try:
            self.send("quit")
        except Exception:
            pass
        try:
            self.proc.wait(timeout=2.0)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2.0)
            except Exception:
                pass


def _parse_option_names(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        if not line.startswith("option "):
            continue
        toks = line.split()
        if "name" not in toks:
            continue
        i = toks.index("name")
        if "type" in toks:
            t = toks.index("type")
            if t > i:
                name = " ".join(toks[i + 1 : t]).strip()
            else:
                name = " ".join(toks[i + 1 :]).strip()
        else:
            name = " ".join(toks[i + 1 :]).strip()
        if name:
            out.add(name)
    return out


def _parse_bestmove(line: str) -> tuple[str, str]:
    toks = line.split()
    if len(toks) < 2:
        return "", ""
    move = toks[1]
    ponder = ""
    if "ponder" in toks:
        i = toks.index("ponder")
        if i + 1 < len(toks):
            ponder = toks[i + 1]
    return move, ponder


def _extract_pv_fallback_move(info_lines: list[str]) -> Optional[str]:
    for line in reversed(info_lines):
        if not line.startswith("info "):
            continue
        toks = line.split()
        if "pv" not in toks:
            continue
        idx = toks.index("pv")
        if idx + 1 >= len(toks):
            continue
        mv = toks[idx + 1]
        if is_usi_move_token(mv):
            return mv
    return None


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:32] or "engine"


def _load_openings(path: Optional[Path], opening_plies: int) -> list[list[str]]:
    max_plies = max(0, opening_plies)
    if path is None:
        return [list(line[:max_plies]) for line in DEFAULT_OPENINGS if line[:max_plies]]

    lines: list[list[str]] = []
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        moves = [tok for tok in line.split() if is_usi_move_token(tok)]
        if not moves:
            continue
        lines.append(moves[:max_plies] if max_plies > 0 else [])
    if not lines:
        return [list(line[:max_plies]) for line in DEFAULT_OPENINGS if line[:max_plies]]
    return lines


def _kif_result(winner: str) -> str:
    if winner == "black":
        return "先手勝ち"
    if winner == "white":
        return "後手勝ち"
    return "引き分け"


def _write_kif(path: Path, *, game_id: str, black_name: str, white_name: str, moves: list[str], winner: str) -> None:
    lines = [
        f"対局ID: {game_id}",
        f"開始日時: {_now_utc()}",
        f"先手: {black_name}",
        f"後手: {white_name}",
        f"結果: {_kif_result(winner)}",
        "",
    ]
    for i, mv in enumerate(moves, start=1):
        lines.append(f"{i} {mv}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class USIEngine:
    def __init__(self, name: str, cmd: list[str], cwd: Path) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.proc = ProcIO(cmd, cwd)
        self.option_names: set[str] = set()

    def initialize(self, options: dict[str, str], *, init_timeout_sec: float = 12.0, ready_timeout_sec: float = 20.0) -> None:
        self.proc.send("usi")
        lines, ok = self.proc.read_until(lambda s: s == "usiok", init_timeout_sec)
        if not ok:
            raise RuntimeError(f"{self.name}: usiok timeout")
        self.option_names = _parse_option_names(lines)
        for k, v in options.items():
            if k in self.option_names:
                self.proc.send(f"setoption name {k} value {v}")
        self.proc.send("isready")
        _, ok = self.proc.read_until(lambda s: s == "readyok", ready_timeout_sec)
        if not ok:
            raise RuntimeError(f"{self.name}: readyok timeout")

    def usinewgame(self) -> None:
        self.proc.send("usinewgame")

    def go(self, position_cmd: str, go_cmd: str, *, timeout_sec: float = 8.0) -> GoResult:
        self.proc.send(position_cmd)
        self.proc.send(go_cmd)
        lines, ok = self.proc.read_until(lambda s: s.startswith("bestmove "), timeout_sec)
        if not ok:
            return GoResult(
                bestmove="resign",
                ponder="",
                raw_bestmove="",
                info_lines=lines,
                timed_out=True,
                engine_dead=not self.proc.alive,
            )
        best_line = ""
        for line in reversed(lines):
            if line.startswith("bestmove "):
                best_line = line
                break
        move, ponder = _parse_bestmove(best_line)
        return GoResult(
            bestmove=move,
            ponder=ponder,
            raw_bestmove=best_line,
            info_lines=lines,
            timed_out=False,
            engine_dead=not self.proc.alive,
        )

    def close(self) -> None:
        self.proc.close()


def _build_backend_options(eval_dir: Path, passthrough: str, *, disable_resign: bool) -> dict[str, str]:
    out: dict[str, str] = {}
    if eval_dir.exists():
        out["EvalDir"] = str(eval_dir)
    out["BookFile"] = "no_book"
    if disable_resign:
        out["ResignValue"] = "32767"
    for chunk in (passthrough or "").split(";"):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = k.strip()
        val = v.strip()
        if key:
            out[key] = val
    return out


def _build_wrapper_options(
    *,
    backend_engine: Path,
    backend_eval: Path,
    wrapper_log_dir: Path,
    swindle_mode: str,
    extra: str,
    disable_resign: bool,
) -> dict[str, str]:
    passthrough_items = ["BookFile=no_book"]
    if backend_eval.exists():
        passthrough_items.append(f"EvalDir={backend_eval}")
    if disable_resign:
        passthrough_items.append("ResignValue=32767")
    passthrough = ";".join(passthrough_items)
    out = {
        "BackendEnginePath": str(backend_engine),
        "BackendEngineOptionPassthrough": passthrough,
        "SwindleDryRun": "false",
        "SwindleEnable": "true",
        "SwindleMode": swindle_mode,
        "SwindleLogEnable": "true",
        "SwindleLogPath": str(wrapper_log_dir),
        "SwindleVerboseInfo": "false",
        "SwindleEmitInfoStringLevel": "0",
        "SwindleShowRanking": "false",
        "SwindleUseMateEngineVerification": "false",
        "SwindleUseDfPn": "false",
        "SwindlePonderEnable": "false",
    }
    for chunk in (extra or "").split(";"):
        part = chunk.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        key = k.strip()
        val = v.strip()
        if key:
            out[key] = val
    return out


def _latest_wrapper_log(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    files = sorted(path.glob("taso-swindle-*.jsonl"))
    if not files:
        return None
    return files[-1]


def _consume_wrapper_log_records(path: Path, cursor: int) -> tuple[int, list[dict[str, object]]]:
    if not path.exists():
        return cursor, []
    data = b""
    with path.open("rb") as fh:
        fh.seek(max(0, int(cursor)))
        data = fh.read()
        new_cursor = int(fh.tell())
    if not data:
        return new_cursor, []
    out: list[dict[str, object]] = []
    for raw in data.decode("utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return new_cursor, out


def _run_pipeline(
    *,
    root: Path,
    wrapper_log: Path,
    kif_dir: Path,
    out_root: Path,
    train_ponder: bool,
    train_hybrid: bool,
    min_outcome_conf: float,
    min_outcome_match_conf: float,
    min_ponder_label_conf: float,
) -> int:
    cmd = [
        "python3",
        "scripts/run_learning_pipeline.py",
        "--logs",
        str(wrapper_log),
        "--kif-dir",
        str(kif_dir),
        "--output-root",
        str(out_root),
        "--ponder-label-mode",
        "runtime_first",
        "--prefer-labeled-outcome",
        "--min-outcome-confidence",
        f"{min_outcome_conf:.3f}",
        "--min-outcome-match-confidence",
        f"{min_outcome_match_conf:.3f}",
        "--min-ponder-label-confidence",
        f"{min_ponder_label_conf:.3f}",
    ]
    if train_ponder:
        cmd.append("--train-ponder")
    else:
        cmd.append("--no-train-ponder")
    if train_hybrid:
        cmd.append("--train-hybrid")
    else:
        cmd.append("--no-train-hybrid")
    done = subprocess.run(cmd, cwd=str(root), text=True)  # noqa: S603,S607
    return int(done.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate self-play games and auto-run learning pipeline.")
    parser.add_argument("--games", type=int, default=200, help="number of games to run")
    parser.add_argument("--max-plies", type=int, default=256, help="max plies per game before draw")
    parser.add_argument("--nodes", type=int, default=400, help="go nodes for both engines (ignored if --movetime-ms > 0)")
    parser.add_argument("--movetime-ms", type=int, default=0, help="go movetime for both engines")
    parser.add_argument("--think-timeout-sec", type=float, default=0.0, help="override per-move timeout seconds (0=auto)")
    parser.add_argument("--game-walltime-sec", type=float, default=0.0, help="hard walltime limit per game seconds (0=disabled)")
    parser.add_argument("--opening-file", default="", help="optional opening lines file (USI moves separated by spaces)")
    parser.add_argument("--opening-plies", type=int, default=4, help="opening plies to preload")
    parser.add_argument("--seed", type=int, default=0, help="random seed (0 means time-based)")
    parser.add_argument("--swap-colors", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--resign-fallback", action=argparse.BooleanOptionalAction, default=True, help="when bestmove is resign, try pv-head move and continue")

    parser.add_argument("--wrapper-cmd", default="python3 -m taso_swindle.main")
    parser.add_argument("--backend-engine", default="./YaneuraOu")
    parser.add_argument("--backend-eval", default="./eval")
    parser.add_argument("--backend-args", default="", help="optional backend startup args")

    parser.add_argument("--wrapper-options", default="", help="extra wrapper setoption string: name=value;...")
    parser.add_argument("--backend-options", default="", help="extra backend setoption string: name=value;...")
    parser.add_argument("--swindle-mode", default="HYBRID", choices=["AUTO", "TACTICAL", "MURKY", "HYBRID"])
    parser.add_argument("--disable-resign", action=argparse.BooleanOptionalAction, default=True, help="set ResignValue high when supported")

    parser.add_argument("--output-root", default="artifacts/selfplay_runs")
    parser.add_argument("--run-id", default="", help="optional fixed run id")

    parser.add_argument("--auto-pipeline", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--train-ponder", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--train-hybrid", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--min-outcome-confidence", type=float, default=0.6)
    parser.add_argument("--min-outcome-match-confidence", type=float, default=0.6)
    parser.add_argument("--min-ponder-label-confidence", type=float, default=0.0)

    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    out_root = Path(args.output_root).expanduser()
    if not out_root.is_absolute():
        out_root = (root / out_root).resolve()
    run_id = args.run_id.strip() or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = out_root / run_id
    games_dir = run_dir / "games_kif"
    wrapper_log_dir = run_dir / "wrapper_logs"
    learning_root = run_dir / "learning_runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    games_dir.mkdir(parents=True, exist_ok=True)
    wrapper_log_dir.mkdir(parents=True, exist_ok=True)

    backend_engine = Path(args.backend_engine).expanduser()
    if not backend_engine.is_absolute():
        backend_engine = (root / backend_engine).resolve()
    if not backend_engine.exists():
        raise SystemExit(f"backend engine not found: {backend_engine}")

    backend_eval = Path(args.backend_eval).expanduser()
    if not backend_eval.is_absolute():
        backend_eval = (root / backend_eval).resolve()

    wrapper_cmd = shlex.split(args.wrapper_cmd)
    if not wrapper_cmd:
        raise SystemExit("wrapper command is empty")
    backend_cmd = [str(backend_engine)] + shlex.split(args.backend_args)

    seed = int(time.time() * 1000) & 0x7FFFFFFF if int(args.seed) == 0 else int(args.seed)
    rng = random.Random(seed)
    opening_path = None
    if args.opening_file.strip():
        p = Path(args.opening_file).expanduser()
        opening_path = p if p.is_absolute() else (root / p).resolve()
        if not opening_path.exists():
            raise SystemExit(f"opening file not found: {opening_path}")
    openings = _load_openings(opening_path, int(args.opening_plies))
    if not openings:
        openings = [[]]

    wrapper_opts = _build_wrapper_options(
        backend_engine=backend_engine,
        backend_eval=backend_eval,
        wrapper_log_dir=wrapper_log_dir,
        swindle_mode=args.swindle_mode,
        extra=args.wrapper_options,
        disable_resign=bool(args.disable_resign),
    )
    backend_opts = _build_backend_options(backend_eval, args.backend_options, disable_resign=bool(args.disable_resign))

    wrapper = USIEngine("wrapper", wrapper_cmd, root)
    backend = USIEngine("backend", backend_cmd, root)
    try:
        wrapper.initialize(wrapper_opts)
        backend.initialize(backend_opts)

        go_cmd = f"go movetime {int(args.movetime_ms)}" if int(args.movetime_ms) > 0 else f"go nodes {max(1, int(args.nodes))}"
        think_timeout = max(2.0, (int(args.movetime_ms) / 1000.0) * 5.0) if int(args.movetime_ms) > 0 else 12.0
        if float(args.think_timeout_sec) > 0:
            think_timeout = max(0.5, float(args.think_timeout_sec))

        stats = {
            "black_win": 0,
            "white_win": 0,
            "draw": 0,
            "resign": 0,
            "resign_fallback": 0,
            "win_decl": 0,
            "timeout": 0,
            "invalid_move": 0,
            "max_plies": 0,
        }

        games_jsonl = run_dir / "games.jsonl"
        with games_jsonl.open("w", encoding="utf-8") as games_out:
            active_wrapper_log: Optional[Path] = None
            wrapper_log_cursor = 0
            for game_idx in range(int(args.games)):
                wrapper.usinewgame()
                backend.usinewgame()

                opening = list(rng.choice(openings)) if openings else []
                moves = list(opening)
                wrapper_is_black = True
                if args.swap_colors and (game_idx % 2 == 1):
                    wrapper_is_black = False

                black_engine = wrapper if wrapper_is_black else backend
                white_engine = backend if wrapper_is_black else wrapper
                black_name = "TASO-SWINDLE" if wrapper_is_black else "YaneuraOu"
                white_name = "YaneuraOu" if wrapper_is_black else "TASO-SWINDLE"

                winner = "draw"
                reason = "max_plies"
                final_move = ""
                timeout_hit = False
                invalid_hit = False
                last_move_black: Optional[str] = None
                last_move_white: Optional[str] = None
                game_started_at = time.time()

                while len(moves) < int(args.max_plies):
                    if float(args.game_walltime_sec) > 0 and (time.time() - game_started_at) > float(args.game_walltime_sec):
                        timeout_hit = True
                        winner = "draw"
                        reason = "game_walltime"
                        break
                    side_black = (len(moves) % 2 == 0)
                    side_engine = black_engine if side_black else white_engine
                    position_cmd = "position startpos"
                    if moves:
                        position_cmd += " moves " + " ".join(moves)

                    go = side_engine.go(position_cmd, go_cmd, timeout_sec=think_timeout)
                    bm = go.bestmove.strip()
                    final_move = bm
                    if go.timed_out:
                        timeout_hit = True
                        winner = "white" if side_black else "black"
                        reason = "timeout"
                        break
                    if is_special_bestmove(bm):
                        if bm == "resign" and bool(args.resign_fallback):
                            alt = _extract_pv_fallback_move(go.info_lines)
                            if alt and is_usi_move_token(alt):
                                bm = alt
                                stats["resign_fallback"] += 1
                            else:
                                winner = "white" if side_black else "black"
                                reason = "resign"
                                break
                        elif bm == "resign":
                            winner = "white" if side_black else "black"
                            reason = "resign"
                            break
                        elif bm == "win":
                            winner = "black" if side_black else "white"
                            reason = "win_decl"
                            break
                        else:
                            winner = "draw"
                            reason = "special"
                            break
                    if not is_usi_move_token(bm):
                        invalid_hit = True
                        winner = "white" if side_black else "black"
                        reason = "invalid_move"
                        break
                    # Same side repeating the exact same move usually indicates
                    # broken position flow (for example an illegal opening prefix).
                    if side_black:
                        if last_move_black == bm:
                            invalid_hit = True
                            winner = "white"
                            reason = "repeat_same_move"
                            break
                        last_move_black = bm
                    else:
                        if last_move_white == bm:
                            invalid_hit = True
                            winner = "black"
                            reason = "repeat_same_move"
                            break
                        last_move_white = bm
                    moves.append(bm)
                else:
                    winner = "draw"
                    reason = "max_plies"

                if reason == "resign":
                    stats["resign"] += 1
                if reason == "win_decl":
                    stats["win_decl"] += 1
                if reason == "timeout":
                    stats["timeout"] += 1
                if reason == "invalid_move":
                    stats["invalid_move"] += 1
                if reason == "max_plies":
                    stats["max_plies"] += 1

                if winner == "black":
                    stats["black_win"] += 1
                elif winner == "white":
                    stats["white_win"] += 1
                else:
                    stats["draw"] += 1

                wrapper_gid: Optional[str] = None
                latest_log = _latest_wrapper_log(wrapper_log_dir)
                if latest_log is not None:
                    if active_wrapper_log is None or active_wrapper_log != latest_log:
                        active_wrapper_log = latest_log
                        wrapper_log_cursor = 0
                    wrapper_log_cursor, chunk = _consume_wrapper_log_records(active_wrapper_log, wrapper_log_cursor)
                    gid_counter: Counter[str] = Counter()
                    for rec in chunk:
                        gid = str(rec.get("game_id", "")).strip()
                        if gid:
                            gid_counter[gid] += 1
                    if gid_counter:
                        wrapper_gid = gid_counter.most_common(1)[0][0]

                game_id = wrapper_gid or f"sp-{run_id}-{game_idx + 1:06d}-{uuid.uuid4().hex[:8]}"
                kif_path = games_dir / f"{game_id}.kif"
                _write_kif(
                    kif_path,
                    game_id=game_id,
                    black_name=black_name,
                    white_name=white_name,
                    moves=moves,
                    winner=winner,
                )

                rec = {
                    "game_id": game_id,
                    "game_index": game_idx + 1,
                    "timestamp": _now_utc(),
                    "wrapper_game_id": wrapper_gid,
                    "wrapper_is_black": wrapper_is_black,
                    "black_name": black_name,
                    "white_name": white_name,
                    "winner": winner,
                    "reason": reason,
                    "moves": moves,
                    "plies": len(moves),
                    "opening_moves": opening,
                    "final_move": final_move,
                    "timeout_hit": timeout_hit,
                    "invalid_hit": invalid_hit,
                }
                games_out.write(json.dumps(rec, ensure_ascii=False) + "\n")

                if (game_idx + 1) % 20 == 0 or (game_idx + 1) == int(args.games):
                    print(
                        f"[selfplay] {game_idx + 1}/{int(args.games)} "
                        f"bw={stats['black_win']} ww={stats['white_win']} d={stats['draw']}"
                    )

        wrapper_log = _latest_wrapper_log(wrapper_log_dir)
        summary = {
            "run_id": run_id,
            "started_at": _now_utc(),
            "seed": seed,
            "games": int(args.games),
            "max_plies": int(args.max_plies),
            "go_cmd": go_cmd,
            "wrapper_cmd": wrapper_cmd,
            "backend_cmd": backend_cmd,
            "wrapper_log_dir": str(wrapper_log_dir),
            "wrapper_log": str(wrapper_log) if wrapper_log else None,
            "games_kif_dir": str(games_dir),
            "games_jsonl": str(games_jsonl),
            "stats": stats,
            "auto_pipeline": bool(args.auto_pipeline),
            "pipeline_rc": None,
            "pipeline_output_root": str(learning_root),
        }

        if args.auto_pipeline:
            if wrapper_log is None:
                print("[selfplay] wrapper log not found; skip pipeline")
                summary["pipeline_rc"] = 2
            else:
                rc = _run_pipeline(
                    root=root,
                    wrapper_log=wrapper_log,
                    kif_dir=games_dir,
                    out_root=learning_root,
                    train_ponder=bool(args.train_ponder),
                    train_hybrid=bool(args.train_hybrid),
                    min_outcome_conf=float(args.min_outcome_confidence),
                    min_outcome_match_conf=float(args.min_outcome_match_confidence),
                    min_ponder_label_conf=float(args.min_ponder_label_confidence),
                )
                summary["pipeline_rc"] = rc
                if rc == 0:
                    print("[selfplay] learning pipeline: OK")
                else:
                    print(f"[selfplay] learning pipeline: FAILED rc={rc}")

        summary_path = run_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[selfplay] done: {summary_path}")
        return 0
    finally:
        wrapper.close()
        backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
