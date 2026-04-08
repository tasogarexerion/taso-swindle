from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .candidate import CandidateMove
from .reply_search import ProbeOutcome, ReplyEval


@dataclass
class PseudoHisshiResult:
    score: float = 0.0
    status: str = "skipped"
    notes: list[str] = field(default_factory=list)


class PseudoHisshiEstimator:
    """Lightweight pseudo-hisshi estimator for Phase2/3 bridge."""

    def __init__(
        self,
        run_probe: Optional[Callable[[str, str], ProbeOutcome]] = None,
        *,
        per_candidate_probe_limit: int = 2,
    ) -> None:
        self.run_probe = run_probe
        self.per_candidate_probe_limit = max(1, int(per_candidate_probe_limit))
        self._round_deadline_ts = 0.0

    def begin_round(self, total_budget_ms: int) -> None:
        if total_budget_ms <= 0:
            self._round_deadline_ts = 0.0
            return
        self._round_deadline_ts = time.time() + (total_budget_ms / 1000.0)

    def estimate(
        self,
        candidate: CandidateMove,
        *,
        root_position_cmd: Optional[str] = None,
        reply_topk: Optional[Iterable[ReplyEval]] = None,
        emergency_fast_mode: bool = False,
        window_ply: int = 6,
        run_probe: Optional[Callable[[str, str], ProbeOutcome]] = None,
        max_probes: Optional[int] = None,
    ) -> float:
        result = self.estimate_with_status(
            candidate,
            root_position_cmd=root_position_cmd,
            reply_topk=reply_topk,
            emergency_fast_mode=emergency_fast_mode,
            window_ply=window_ply,
            run_probe=run_probe,
            max_probes=max_probes,
        )
        return result.score

    def estimate_with_status(
        self,
        candidate: CandidateMove,
        *,
        root_position_cmd: Optional[str] = None,
        reply_topk: Optional[Iterable[ReplyEval]] = None,
        emergency_fast_mode: bool = False,
        window_ply: int = 6,
        run_probe: Optional[Callable[[str, str], ProbeOutcome]] = None,
        max_probes: Optional[int] = None,
    ) -> PseudoHisshiResult:
        if emergency_fast_mode:
            return PseudoHisshiResult(score=0.0, status="skipped", notes=["emergency_fast_mode"])

        replies = list(reply_topk) if reply_topk is not None else list(getattr(candidate, "reply_topk", []) or [])
        if not replies:
            return PseudoHisshiResult(score=0.0, status="skipped", notes=["no_reply_topk"])

        runner = run_probe or self.run_probe
        if runner is None or not root_position_cmd:
            return PseudoHisshiResult(score=0.0, status="skipped", notes=["probe_not_configured"])

        if self._round_deadline_ts > 0.0 and time.time() >= self._round_deadline_ts:
            return PseudoHisshiResult(score=0.0, status="skipped_budget", notes=["round_budget_exhausted"])

        probe_cap = self.per_candidate_probe_limit if max_probes is None else max(1, int(max_probes))
        depth = max(4, min(10, 4 + max(1, int(window_ply)) // 2))

        attempts = 0
        mate_hits = 0
        cp_spikes = 0
        danger_hits = 0
        timed_out = 0
        budget_cut = False
        notes: list[str] = []

        for idx, reply in enumerate(replies[:probe_cap]):
            if not reply.move:
                continue

            if self._round_deadline_ts > 0.0:
                remain_sec = self._round_deadline_ts - time.time()
                if remain_sec <= 0.0:
                    budget_cut = True
                    notes.append("round_budget_exhausted")
                    break
                remain_ms = max(1, int(remain_sec * 1000))
                remain_slots = max(1, probe_cap - idx)
                per_probe_ms = max(30, min(220, remain_ms // remain_slots))
                go_cmd = f"go movetime {per_probe_ms}"
            else:
                go_cmd = f"go depth {depth}"

            position = _append_move(_append_move(root_position_cmd, candidate.move), reply.move)
            try:
                outcome = runner(position, go_cmd)
            except Exception:
                notes.append("probe_exception")
                continue

            attempts += 1
            if outcome.timed_out:
                timed_out += 1
                notes.append("probe_timeout")
                continue

            top = outcome.info_result.by_multipv.get(1)
            if top is None:
                continue
            if top.mate is not None and top.mate > 0:
                mate_hits += 1
                continue
            if top.mate is not None and top.mate < 0:
                danger_hits += 1
                continue
            if top.cp is not None and top.cp >= 800:
                cp_spikes += 1
            elif top.cp is not None and top.cp <= -1200:
                danger_hits += 1

        if attempts <= 0:
            if budget_cut:
                return PseudoHisshiResult(score=0.0, status="skipped_budget", notes=notes)
            if timed_out > 0:
                return PseudoHisshiResult(score=0.0, status="timeout", notes=notes)
            notes.append("no_successful_probe")
            return PseudoHisshiResult(score=0.0, status="skipped", notes=notes)

        raw = 0.75 * (mate_hits / attempts) + 0.25 * (cp_spikes / attempts) - 0.40 * (danger_hits / attempts)
        if not math.isfinite(raw):
            raw = 0.0
        score = _clamp(raw)
        if budget_cut:
            return PseudoHisshiResult(score=score, status="skipped_budget", notes=notes)
        if timed_out >= attempts and score <= 0.0:
            return PseudoHisshiResult(score=0.0, status="timeout", notes=notes)
        return PseudoHisshiResult(score=score, status="ok", notes=notes)


def _append_move(position_cmd: str, move: str) -> str:
    if not move:
        return position_cmd
    if " moves " in position_cmd:
        return f"{position_cmd} {move}"
    return f"{position_cmd} moves {move}"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
