from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..info_parser import InfoParseResult


@dataclass
class ProbeOutcome:
    info_result: InfoParseResult
    timed_out: bool = False
    backend_dead: bool = False
    quit_requested: bool = False
    deferred_commands: list[str] = field(default_factory=list)
    bestmove: Optional[str] = None


@dataclass(frozen=True)
class ReplyEval:
    move: str
    multipv: int
    pv: list[str]
    cp_raw: Optional[int]
    mate_raw: Optional[int]
    opp_utility: float
    root_cp: Optional[int]
    root_mate: Optional[int]
    is_check_like: bool
    is_flashy_like: bool


@dataclass
class ReplySearchResult:
    reply_topk: list[ReplyEval] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    option_restore_failed: bool = False
    timed_out: bool = False
    backend_dead: bool = False
    quit_requested: bool = False
    deferred_commands: list[str] = field(default_factory=list)
    bestmove: Optional[str] = None


class ReplySearch:
    """
    Stage2 reply probe helper.

    Phase2 rule:
    - Probe-side backend option mutation is restricted to MultiPV only.
    - Modified option must be restored in finally.
    """

    def __init__(
        self,
        set_backend_option: Optional[Callable[[str, str], None]] = None,
        run_probe: Optional[Callable[[str, str], ProbeOutcome]] = None,
    ) -> None:
        self.set_backend_option = set_backend_option
        self.run_probe = run_probe

    def analyze(
        self,
        *,
        position_cmd: str = "",
        go_cmd: str = "",
        original_multipv: Optional[int] = None,
        probe_multipv: Optional[int] = None,
        reply_topk: int = 4,
    ) -> ReplySearchResult:
        result = ReplySearchResult()

        changed = (
            self.set_backend_option is not None
            and original_multipv is not None
            and probe_multipv is not None
            and original_multipv != probe_multipv
        )

        try:
            if changed:
                self.set_backend_option("MultiPV", str(probe_multipv))

            if self.run_probe is None:
                return result

            if not go_cmd:
                go_cmd = "go depth 10"

            outcome = self.run_probe(position_cmd, go_cmd)
            result.timed_out = outcome.timed_out
            result.backend_dead = outcome.backend_dead
            result.quit_requested = outcome.quit_requested
            result.deferred_commands = list(outcome.deferred_commands)
            result.bestmove = outcome.bestmove
            if outcome.backend_dead:
                result.events.append("BACKEND restart")

            if outcome.info_result.by_multipv:
                for idx in sorted(outcome.info_result.by_multipv.keys()):
                    if len(result.reply_topk) >= max(1, reply_topk):
                        break
                    snap = outcome.info_result.by_multipv[idx]
                    if snap.move:
                        result.reply_topk.append(self._to_reply_eval(snap))

            return result
        finally:
            if changed:
                try:
                    # Required invariant: probe side MultiPV must always be restored.
                    assert original_multipv is not None
                    self.set_backend_option("MultiPV", str(original_multipv))
                except Exception:
                    result.events.append("restore_failed:MultiPV")
                    result.option_restore_failed = True

    def _to_reply_eval(self, snap) -> ReplyEval:
        cp_raw = snap.cp
        mate_raw = snap.mate
        opp_utility = self._opp_utility(cp_raw=cp_raw, mate_raw=mate_raw)
        root_cp = (-cp_raw) if cp_raw is not None else None
        root_mate = (-mate_raw) if mate_raw is not None else None
        move = snap.move or ""
        return ReplyEval(
            move=move,
            multipv=snap.multipv,
            pv=list(snap.pv),
            cp_raw=cp_raw,
            mate_raw=mate_raw,
            opp_utility=opp_utility,
            root_cp=root_cp,
            root_mate=root_mate,
            is_check_like=self._is_check_like(move, snap.pv),
            is_flashy_like=self._is_flashy_like(move),
        )

    def _opp_utility(self, cp_raw: Optional[int], mate_raw: Optional[int]) -> float:
        if mate_raw is not None:
            if mate_raw > 0:
                return 120_000.0 - float(mate_raw * 1_000)
            return -120_000.0 + float(abs(mate_raw) * 1_000)
        if cp_raw is None:
            return -1e9
        return float(cp_raw)

    def _is_check_like(self, move: str, pv: list[str]) -> bool:
        m = move.strip()
        if not m:
            return False
        if m.endswith("+"):
            return True
        if m.startswith(("R*", "B*")):
            return True
        if pv and len(pv) >= 2 and pv[0].endswith("+"):
            return True
        return False

    def _is_flashy_like(self, move: str) -> bool:
        m = move.strip().upper()
        if not m:
            return False
        if "*" in m:
            return True
        if m.endswith("+"):
            return True
        return m.startswith(("2", "8"))
