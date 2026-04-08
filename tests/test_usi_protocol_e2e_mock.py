from __future__ import annotations

import json
import os
import queue
import stat
import subprocess
import tempfile
import textwrap
import threading
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FAKE_ENGINE = textwrap.dedent(
    r"""
    #!/usr/bin/env python3
    import sys
    import time

    multipv = 1
    position_cmd = "position startpos"
    verify_sleep_ms = 0
    crash_mode = False
    infinite = False
    ponder_mode = False

    def flush(line: str) -> None:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def parse_setoption(line: str):
        toks = line.strip().split()
        if len(toks) < 4 or toks[0] != "setoption":
            return None, None
        if "name" not in toks:
            return None, None
        i = toks.index("name")
        if "value" in toks:
            j = toks.index("value")
            name = " ".join(toks[i+1:j])
            value = " ".join(toks[j+1:])
        else:
            name = " ".join(toks[i+1:])
            value = ""
        return name, value

    def emit_stage(position: str):
        # root
        if position.strip() == "position startpos":
            flush("info depth 14 multipv 1 score cp -520 pv 2g2f 8c8d")
            if multipv >= 2:
                flush("info depth 14 multipv 2 score cp -640 pv 7g7f 3c3d")
            return "2g2f", "8c8d"

        # candidate probe
        if position.endswith(" 7g7f"):
            flush("info depth 10 multipv 1 score cp 450 pv R*2b")
            if multipv >= 2:
                flush("info depth 10 multipv 2 score cp -220 pv 7c7d")
            if multipv >= 3:
                flush("info depth 10 multipv 3 score cp -420 pv 8c8d")
            return "R*2b", None

        if position.endswith(" 2g2f"):
            flush("info depth 10 multipv 1 score cp 700 pv 7c7d")
            if multipv >= 2:
                flush("info depth 10 multipv 2 score cp 620 pv 8c8d")
            if multipv >= 3:
                flush("info depth 10 multipv 3 score cp 590 pv 3c3d")
            return "7c7d", None

        flush("info depth 8 multipv 1 score cp 0 pv 7g7f")
        return "7g7f", None

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        if line == "usi":
            flush("id name FakeUSI")
            flush("id author test")
            flush("option name MultiPV type spin default 1 min 1 max 8")
            flush("option name EvalDir type string default eval")
            flush("option name BookFile type string default no_book")
            flush("option name VerifySleepMs type spin default 0 min 0 max 10000")
            flush("option name CrashMode type check default false")
            flush("usiok")
            continue
        if line == "isready":
            flush("readyok")
            continue
        if line.startswith("setoption"):
            name, value = parse_setoption(line)
            if name == "MultiPV":
                try:
                    multipv = max(1, int(value))
                except Exception:
                    multipv = 1
            elif name == "VerifySleepMs":
                try:
                    verify_sleep_ms = max(0, int(value))
                except Exception:
                    verify_sleep_ms = 0
            elif name == "CrashMode":
                crash_mode = value.strip().lower() in {"true", "1", "yes", "on"}
            continue
        if line == "usinewgame":
            infinite = False
            ponder_mode = False
            continue
        if line.startswith("position "):
            position_cmd = line
            continue
        if line.startswith("go"):
            if crash_mode:
                sys.exit(91)
            if "ponder" in line.split():
                ponder_mode = True
                infinite = True
                continue
            if "infinite" in line.split():
                infinite = True
                continue
            if verify_sleep_ms > 0:
                time.sleep(verify_sleep_ms / 1000.0)
            move, ponder = emit_stage(position_cmd)
            if ponder:
                flush(f"bestmove {move} ponder {ponder}")
            else:
                flush(f"bestmove {move}")
            continue
        if line == "stop":
            if infinite:
                infinite = False
                ponder_mode = False
                flush("bestmove 7g7f")
            continue
        if line == "ponderhit":
            if infinite and ponder_mode:
                infinite = False
                ponder_mode = False
                move, ponder = emit_stage(position_cmd)
                if ponder:
                    flush(f"bestmove {move} ponder {ponder}")
                else:
                    flush(f"bestmove {move}")
            continue
        if line == "quit":
            break
    """
)


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
        self._alive = True
        self._t = threading.Thread(target=self._reader, daemon=True)
        self._t.start()

    def _reader(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._q.put(line.rstrip("\n"))
        self._alive = False

    def send(self, line: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def read_until(self, pred, timeout: float = 10.0) -> tuple[list[str], bool]:
        out: list[str] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._q.get(timeout=0.05)
            except queue.Empty:
                if self.proc.poll() is not None and not self._alive:
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
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)


def _write_fake_engine(tmp: Path) -> Path:
    path = tmp / "fake_usi_engine.py"
    path.write_text(FAKE_ENGINE, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_fake_dfpn(tmp: Path) -> Path:
    code = textwrap.dedent(
        """\
        #!/usr/bin/env python3
        import sys
        import time

        if "--sleep-ms" in sys.argv:
            i = sys.argv.index("--sleep-ms")
            if i + 1 < len(sys.argv):
                try:
                    time.sleep(max(0, int(sys.argv[i + 1])) / 1000.0)
                except Exception:
                    pass
        print("unknown")
        """
    )
    path = tmp / "fake_dfpn.py"
    path.write_text(code, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _write_ponder_gate_weights(tmp: Path, *, bias: float = 0.2, version: str = "v1") -> Path:
    payload = {
        "version": 1,
        "kind": "ponder_gate_adjustment",
        "source": "test",
        "label_mode": "heuristic",
        "trained_samples": 1,
        "features_version": version,
        "threshold_suggested": 0.55,
        "weights": {
            "bias": float(bias),
            "reply_coverage": 0.05,
            "candidate_count": 0.02,
        },
    }
    path = tmp / "ponder_gate_weights.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _boot_wrapper(
    proc: ProcIO,
    *,
    backend_path: str,
    passthrough: str = "BookFile=no_book",
    dry_run: bool = True,
    verify: bool = False,
    verify_ms: int = 80,
    verify_mode: str = "VERIFY_ONLY",
    verify_hybrid_policy: str = "CONSERVATIVE",
    use_dfpn: bool = False,
    dfpn_path: str = "",
    dfpn_ms: int = 60,
    dfpn_parser_mode: str = "AUTO",
    ponder_enable: bool = False,
    ponder_verify: bool = False,
    ponder_dfpn: bool = False,
    ponder_max_ms: int = 500,
    ponder_reuse_min_score: int = 55,
    ponder_cache_max_age_ms: int = 3000,
    ponder_require_verify_for_mate_cache: bool = True,
    ponder_gate_weights_path: str = "",
    use_ponder_gate_learned_adjustment: bool = False,
    ponder_reuse_learned_adjustment_cap_pct: int = 20,
) -> None:
    proc.send(f"setoption name BackendEnginePath value {sys.executable}")
    proc.send(f"setoption name BackendEngineArgs value {backend_path}")
    proc.send("setoption name SwindleEnable value true")
    proc.send("setoption name SwindleMultiPV value 2")
    proc.send("setoption name SwindleReplyMultiPV value 3")
    proc.send("setoption name SwindleReplyTopK value 3")
    proc.send(f"setoption name SwindleDryRun value {'true' if dry_run else 'false'}")
    proc.send(f"setoption name BackendEngineOptionPassthrough value {passthrough}")
    proc.send(f"setoption name SwindleUseMateEngineVerification value {'true' if verify else 'false'}")
    proc.send(f"setoption name SwindleMateVerifyTimeMs value {verify_ms}")
    proc.send(f"setoption name SwindleVerifyMode value {verify_mode}")
    proc.send(f"setoption name SwindleVerifyHybridPolicy value {verify_hybrid_policy}")
    proc.send(f"setoption name SwindleUseDfPn value {'true' if use_dfpn else 'false'}")
    proc.send(f"setoption name SwindleDfPnPath value {dfpn_path}")
    proc.send(f"setoption name SwindleDfPnTimeMs value {dfpn_ms}")
    proc.send(f"setoption name SwindleDfPnParserMode value {dfpn_parser_mode}")
    proc.send(f"setoption name SwindlePonderEnable value {'true' if ponder_enable else 'false'}")
    proc.send(f"setoption name SwindlePonderVerify value {'true' if ponder_verify else 'false'}")
    proc.send(f"setoption name SwindlePonderDfPn value {'true' if ponder_dfpn else 'false'}")
    proc.send(f"setoption name SwindlePonderMaxMs value {ponder_max_ms}")
    proc.send(f"setoption name SwindlePonderReuseMinScore value {ponder_reuse_min_score}")
    proc.send(f"setoption name SwindlePonderCacheMaxAgeMs value {ponder_cache_max_age_ms}")
    proc.send(
        f"setoption name SwindlePonderRequireVerifyForMateCache value {'true' if ponder_require_verify_for_mate_cache else 'false'}"
    )
    proc.send(f"setoption name SwindlePonderGateWeightsPath value {ponder_gate_weights_path}")
    proc.send(
        f"setoption name SwindleUsePonderGateLearnedAdjustment value {'true' if use_ponder_gate_learned_adjustment else 'false'}"
    )
    proc.send(
        f"setoption name SwindlePonderReuseLearnedAdjustmentCapPct value {ponder_reuse_learned_adjustment_cap_pct}"
    )
    proc.send("usi")
    _, ok = proc.read_until(lambda s: s == "usiok", timeout=8.0)
    assert ok
    proc.send("isready")
    _, ok = proc.read_until(lambda s: s == "readyok", timeout=10.0)
    assert ok


def test_usi_go_dryrun_true() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(proc, backend_path=str(fake), dry_run=True)
            proc.send("usinewgame")
            proc.send("position startpos")
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            best = [x for x in lines if x.startswith("bestmove ")][-1]
            assert best.split()[1] == "2g2f"
        finally:
            proc.close()


def test_usi_go_dryrun_false() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(proc, backend_path=str(fake), dry_run=False)
            proc.send("usinewgame")
            proc.send("position startpos")
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            best = [x for x in lines if x.startswith("bestmove ")][-1]
            assert best.split()[1] in {"2g2f", "7g7f"}
        finally:
            proc.close()


def test_verify_timeout_continues() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=True,
                verify_ms=40,
                passthrough="BookFile=no_book;VerifySleepMs=220",
            )
            proc.send("usinewgame")
            proc.send("position startpos")
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=12.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
        finally:
            proc.close()


def test_long_go_stop_returns_bestmove() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(proc, backend_path=str(fake), dry_run=True)

            proc.send("position startpos")
            proc.send("go infinite")
            time.sleep(0.1)
            proc.send("stop")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=8.0)
            assert ok
        finally:
            proc.close()


def test_ponderhit_no_hang() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(proc, backend_path=str(fake), dry_run=True)
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=8.0)
            assert ok
        finally:
            proc.close()


def test_backend_restart_chain_fallback() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(proc, backend_path=str(fake), dry_run=True)
            proc.send("setoption name CrashMode value true")
            proc.send("position startpos")
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            crash_best = [x for x in lines if x.startswith("bestmove ")][-1]
            assert crash_best.split()[1] in {"resign", "2g2f", "7g7f"}

            # crash again (restart chain)
            proc.send("position startpos")
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            crash_best2 = [x for x in lines if x.startswith("bestmove ")][-1]
            assert crash_best2.split()[1] in {"resign", "2g2f", "7g7f"}

            # recover path
            proc.send("setoption name CrashMode value false")
            proc.send("position startpos")
            proc.send("go movetime 120")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
        finally:
            proc.close()


def test_verify_and_dfpn_timeout_still_returns_bestmove() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s3-e2e-") as td:
        tmp = Path(td)
        fake = _write_fake_engine(tmp)
        dfpn = _write_fake_dfpn(tmp)
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=True,
                verify_mode="TOP_CANDIDATES",
                verify_ms=50,
                use_dfpn=True,
                dfpn_path=f"{sys.executable} {dfpn} --sleep-ms 220",
                dfpn_ms=10,
                passthrough="BookFile=no_book;VerifySleepMs=250",
            )
            proc.send("position startpos")
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=12.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
        finally:
            proc.close()


def test_ponder_timeout_event_only() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s5-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=True,
                verify_mode="VERIFY_ONLY",
                ponder_enable=True,
                ponder_verify=True,
                ponder_dfpn=False,
                ponder_max_ms=0,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
        finally:
            proc.close()


def test_ponder_backend_crash_fallback_bestmove() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s5-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=False,
                ponder_enable=True,
                ponder_verify=False,
                ponder_max_ms=120,
            )
            proc.send("setoption name CrashMode value true")
            proc.send("position startpos")
            proc.send("go ponder")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            best = [x for x in lines if x.startswith("bestmove ")][-1]
            assert best.split()[1] in {"resign", "2g2f", "7g7f"}
        finally:
            proc.close()


def test_ponderhit_cache_hit_no_hang() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s6-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=True,
                verify_mode="VERIFY_ONLY",
                ponder_enable=True,
                ponder_verify=True,
                ponder_dfpn=False,
                ponder_max_ms=500,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok

            # Same position immediate go should consume ponder cache safely.
            proc.send("go movetime 120")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
        finally:
            proc.close()


def test_ponderhit_quality_gate_allows_reuse() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=True,
                verify_mode="VERIFY_ONLY",
                ponder_enable=True,
                ponder_verify=True,
                ponder_dfpn=False,
                ponder_max_ms=500,
                ponder_reuse_min_score=0,
                ponder_cache_max_age_ms=8000,
                ponder_require_verify_for_mate_cache=False,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok

            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            assert any("ponder_cache_hit" in s for s in lines if s.startswith("info string "))
        finally:
            proc.close()


def test_ponderhit_quality_gate_blocks_reuse_but_no_hang() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s7-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=False,
                verify_mode="VERIFY_ONLY",
                ponder_enable=True,
                ponder_verify=False,
                ponder_dfpn=False,
                ponder_max_ms=500,
                ponder_reuse_min_score=99,
                ponder_cache_max_age_ms=8000,
                ponder_require_verify_for_mate_cache=True,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok

            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
            assert any("ponder_cache_" in s for s in lines if s.startswith("info string "))
        finally:
            proc.close()


def test_ponderhit_learned_adjustment_path_no_hang() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-e2e-") as td:
        tmp = Path(td)
        fake = _write_fake_engine(tmp)
        weights = _write_ponder_gate_weights(tmp, bias=0.25, version="v1")
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=False,
                ponder_enable=True,
                ponder_verify=False,
                ponder_dfpn=False,
                ponder_max_ms=500,
                ponder_reuse_min_score=40,
                ponder_cache_max_age_ms=8000,
                ponder_require_verify_for_mate_cache=False,
                ponder_gate_weights_path=str(weights),
                use_ponder_gate_learned_adjustment=True,
                ponder_reuse_learned_adjustment_cap_pct=20,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok

            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
        finally:
            proc.close()


def test_ponderhit_quality_gate_hard_fail_ignores_learning() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s8-e2e-") as td:
        tmp = Path(td)
        fake = _write_fake_engine(tmp)
        weights = _write_ponder_gate_weights(tmp, bias=0.9, version="v1")
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=False,
                ponder_enable=True,
                ponder_verify=False,
                ponder_dfpn=False,
                ponder_max_ms=500,
                ponder_reuse_min_score=0,
                ponder_cache_max_age_ms=30,
                ponder_require_verify_for_mate_cache=False,
                ponder_gate_weights_path=str(weights),
                use_ponder_gate_learned_adjustment=True,
                ponder_reuse_learned_adjustment_cap_pct=50,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok

            # Force hard fail by making cache stale before the next go.
            time.sleep(0.08)
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
            assert any("ponder_cache_stale" in s for s in lines if s.startswith("info string "))
        finally:
            proc.close()


def test_usi_protocol_e2e_runtime_label_does_not_break_bestmove() -> None:
    with tempfile.TemporaryDirectory(prefix="taso-s9-e2e-") as td:
        fake = _write_fake_engine(Path(td))
        proc = ProcIO([sys.executable, "-m", "taso_swindle.main"], cwd=str(ROOT))
        try:
            _boot_wrapper(
                proc,
                backend_path=str(fake),
                dry_run=False,
                verify=True,
                verify_mode="VERIFY_ONLY",
                ponder_enable=True,
                ponder_verify=True,
                ponder_dfpn=False,
                ponder_max_ms=500,
                ponder_reuse_min_score=0,
                ponder_cache_max_age_ms=8000,
                ponder_require_verify_for_mate_cache=False,
            )
            proc.send("position startpos")
            proc.send("go ponder")
            time.sleep(0.1)
            proc.send("ponderhit")
            _, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok

            # Runtime label path is evaluated on cache reuse in this non-ponder go.
            proc.send("go movetime 120")
            lines, ok = proc.read_until(lambda s: s.startswith("bestmove "), timeout=10.0)
            assert ok
            assert any(s.startswith("bestmove ") for s in lines)
        finally:
            proc.close()


def test_ponder_crash_fallback_keeps_bestmove() -> None:
    test_ponder_backend_crash_fallback_bestmove()


if __name__ == "__main__":
    test_usi_go_dryrun_true()
    test_usi_go_dryrun_false()
    test_verify_timeout_continues()
    test_long_go_stop_returns_bestmove()
    test_ponderhit_no_hang()
    test_backend_restart_chain_fallback()
    test_verify_and_dfpn_timeout_still_returns_bestmove()
    test_ponder_timeout_event_only()
    test_ponder_backend_crash_fallback_bestmove()
    test_ponderhit_cache_hit_no_hang()
    test_ponderhit_quality_gate_allows_reuse()
    test_ponderhit_quality_gate_blocks_reuse_but_no_hang()
    test_ponderhit_learned_adjustment_path_no_hang()
    test_ponderhit_quality_gate_hard_fail_ignores_learning()
    test_usi_protocol_e2e_runtime_label_does_not_break_bestmove()
    test_ponder_crash_fallback_keeps_bestmove()
    print("ok test_usi_protocol_e2e_mock")
