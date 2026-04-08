#!/usr/bin/env python3
from __future__ import annotations

import argparse
import queue
import shlex
import subprocess
import threading
import time
from pathlib import Path


class ProcIO:
    def __init__(self, cmd: list[str], cwd: str) -> None:
        self.proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self._q: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._q.put(line.rstrip("\n"))

    def send(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def read_until(self, pred, timeout: float) -> tuple[list[str], bool]:
        out: list[str] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._q.get(timeout=0.05)
            except queue.Empty:
                if self.proc.poll() is not None:
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
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2.0)


def parse_option_names(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        if not line.startswith("option "):
            continue
        toks = line.split()
        if "name" not in toks:
            continue
        i = toks.index("name")
        j = toks.index("type") if "type" in toks and toks.index("type") > i else len(toks)
        name = " ".join(toks[i + 1 : j]).strip()
        if name:
            out.add(name)
    return out


def smoke_engine(engine_cmd: list[str], eval_dir: Path, movetime_ms: int, cwd: Path) -> bool:
    print(f"[smoke] backend cmd: {engine_cmd}")
    proc = ProcIO(engine_cmd, str(cwd))
    try:
        proc.send("usi")
        usi_lines, ok = proc.read_until(lambda s: s == "usiok", timeout=8.0)
        if not ok:
            print("[smoke] backend: usiok timeout")
            return False
        opt_names = parse_option_names(usi_lines)
        if "BookFile" in opt_names:
            proc.send("setoption name BookFile value no_book")
        if "EvalDir" in opt_names and eval_dir.exists():
            proc.send(f"setoption name EvalDir value {eval_dir}")
        proc.send("isready")
        _, ok = proc.read_until(lambda s: s == "readyok", timeout=12.0)
        if not ok:
            print("[smoke] backend: readyok timeout")
            return False
        proc.send("usinewgame")
        proc.send("position startpos")
        proc.send(f"go movetime {movetime_ms}")
        lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=12.0)
        if not ok:
            print("[smoke] backend: bestmove timeout")
            return False
        best = [x for x in lines if x.startswith("bestmove ")][-1]
        print(f"[smoke] backend bestmove: {best}")
        return True
    finally:
        proc.close()


def smoke_wrapper(
    wrapper_cmd_str: str,
    engine_path: Path,
    eval_dir: Path,
    movetime_ms: int,
    cwd: Path,
    *,
    verify_mode: str,
    verify_hybrid_policy: str,
    mate_profile: str,
    ponder: bool,
    mate_engine: Path | None,
    mate_eval: Path | None,
    dfpn_cmd: str,
) -> bool:
    wrapper_cmd = shlex.split(wrapper_cmd_str)
    if not wrapper_cmd:
        print("[smoke] wrapper command is empty after shlex.split()")
        return False

    print(f"[smoke] wrapper cmd: {wrapper_cmd}")
    proc = ProcIO(wrapper_cmd, str(cwd))
    try:
        proc.send(f"setoption name BackendEnginePath value {engine_path}")
        passthrough = "BookFile=no_book"
        if eval_dir.exists():
            passthrough += f";EvalDir={eval_dir}"
        proc.send(f"setoption name BackendEngineOptionPassthrough value {passthrough}")
        proc.send("setoption name SwindleDryRun value true")
        proc.send("setoption name SwindleUseMateEngineVerification value true")
        proc.send(f"setoption name SwindleVerifyMode value {verify_mode}")
        proc.send(f"setoption name SwindleVerifyHybridPolicy value {verify_hybrid_policy}")
        proc.send(f"setoption name SwindleMateEngineProfile value {mate_profile}")
        proc.send(f"setoption name SwindlePonderEnable value {'true' if ponder else 'false'}")
        if mate_engine is not None:
            proc.send(f"setoption name SwindleMateEnginePath value {mate_engine}")
        if mate_eval is not None:
            proc.send(f"setoption name SwindleMateEngineEvalDir value {mate_eval}")
        if dfpn_cmd.strip():
            proc.send("setoption name SwindleUseDfPn value true")
            proc.send(f"setoption name SwindleDfPnPath value {dfpn_cmd}")
            proc.send("setoption name SwindleDfPnTimeMs value 80")
        else:
            proc.send("setoption name SwindleUseDfPn value false")
        proc.send("usi")
        _, ok = proc.read_until(lambda s: s == "usiok", timeout=10.0)
        if not ok:
            print("[smoke] wrapper: usiok timeout")
            return False
        proc.send("isready")
        _, ok = proc.read_until(lambda s: s == "readyok", timeout=14.0)
        if not ok:
            print("[smoke] wrapper: readyok timeout")
            return False
        proc.send("usinewgame")
        proc.send("position startpos")
        proc.send(f"go movetime {movetime_ms}")
        lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=14.0)
        if not ok:
            print("[smoke] wrapper: bestmove timeout")
            return False
        best = [x for x in lines if x.startswith("bestmove ")][-1]
        print(f"[smoke] wrapper bestmove: {best}")
        return True
    finally:
        proc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for real USI backend and TASO-SWINDLE wrapper.")
    parser.add_argument("--engine", default="./YaneuraOu", help="backend engine executable path")
    parser.add_argument("--eval", default="./eval", help="eval directory path")
    parser.add_argument("--wrapper", required=True, help='wrapper command as a single string, e.g. "python3 -m taso_swindle.main"')
    parser.add_argument("--verify-mode", default="VERIFY_ONLY", choices=["VERIFY_ONLY", "TOP_CANDIDATES", "AGGRESSIVE"])
    parser.add_argument(
        "--verify-hybrid-policy",
        default="CONSERVATIVE",
        choices=["CONSERVATIVE", "BALANCED", "MATE_ENGINE_FIRST", "DFPN_FIRST"],
    )
    parser.add_argument("--mate-profile", default="AUTO", choices=["AUTO", "SAFE", "FAST_VERIFY"])
    parser.add_argument("--ponder", action="store_true", help="enable ponder safety path for wrapper smoke")
    parser.add_argument("--mate-engine", default="", help="optional dedicated mate engine path")
    parser.add_argument("--mate-eval", default="", help="optional dedicated mate engine eval dir")
    parser.add_argument("--dfpn", default="", help="optional dfpn command string (can include args)")
    parser.add_argument("--movetime", type=int, default=300, help="movetime in ms for smoke go command")
    args = parser.parse_args()

    cwd = Path.cwd()
    engine_path = Path(args.engine).expanduser()
    if not engine_path.is_absolute():
        engine_path = (cwd / engine_path).resolve()
    eval_dir = Path(args.eval).expanduser()
    if not eval_dir.is_absolute():
        eval_dir = (cwd / eval_dir).resolve()
    mate_engine = None
    if args.mate_engine.strip():
        p = Path(args.mate_engine).expanduser()
        mate_engine = p if p.is_absolute() else (cwd / p).resolve()
    mate_eval = None
    if args.mate_eval.strip():
        p = Path(args.mate_eval).expanduser()
        mate_eval = p if p.is_absolute() else (cwd / p).resolve()

    engine_cmd = [str(engine_path)]
    ok_backend = smoke_engine(engine_cmd, eval_dir, args.movetime, cwd)
    ok_wrapper = smoke_wrapper(
        args.wrapper,
        engine_path,
        eval_dir,
        args.movetime,
        cwd,
        verify_mode=args.verify_mode,
        verify_hybrid_policy=args.verify_hybrid_policy,
        mate_profile=args.mate_profile,
        ponder=args.ponder,
        mate_engine=mate_engine,
        mate_eval=mate_eval,
        dfpn_cmd=args.dfpn,
    )

    if ok_backend and ok_wrapper:
        print("[smoke] OK")
        return 0
    print("[smoke] FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
