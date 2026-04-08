from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .usi_messages import parse_mate_token


@dataclass
class InfoSnapshot:
    multipv: int = 1
    depth: Optional[int] = None
    seldepth: Optional[int] = None
    cp: Optional[int] = None
    mate: Optional[int] = None
    nodes: Optional[int] = None
    nps: Optional[int] = None
    hashfull: Optional[int] = None
    time_ms: Optional[int] = None
    pv: list[str] = field(default_factory=list)
    move: Optional[str] = None
    raw_line: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class InfoParseResult:
    by_multipv: Dict[int, InfoSnapshot] = field(default_factory=dict)
    by_move: Dict[str, InfoSnapshot] = field(default_factory=dict)

    def upsert(self, snapshot: InfoSnapshot) -> None:
        self.by_multipv[snapshot.multipv] = snapshot

        if not snapshot.move:
            return

        prev = self.by_move.get(snapshot.move)
        if prev is None:
            self.by_move[snapshot.move] = snapshot
            return

        prev_depth = prev.depth or 0
        new_depth = snapshot.depth or 0
        if new_depth > prev_depth:
            self.by_move[snapshot.move] = snapshot
            return

        if new_depth == prev_depth:
            prev_mate = prev.mate
            new_mate = snapshot.mate
            if prev_mate is None and new_mate is not None:
                self.by_move[snapshot.move] = snapshot
                return
            if prev_mate is not None and new_mate is not None and abs(new_mate) < abs(prev_mate):
                self.by_move[snapshot.move] = snapshot
                return

            prev_cp = prev.cp if prev.cp is not None else -10**9
            new_cp = snapshot.cp if snapshot.cp is not None else -10**9
            if new_cp > prev_cp:
                self.by_move[snapshot.move] = snapshot


class InfoParser:
    def parse_line(self, line: str) -> Optional[InfoSnapshot]:
        if not line.startswith("info"):
            return None

        tokens = line.split()
        if len(tokens) < 2:
            return None

        snap = InfoSnapshot(raw_line=line)

        i = 1
        while i < len(tokens):
            tok = tokens[i]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None

            if tok == "depth" and nxt is not None:
                snap.depth = _try_int(nxt)
                i += 2
                continue
            if tok == "seldepth" and nxt is not None:
                snap.seldepth = _try_int(nxt)
                i += 2
                continue
            if tok == "multipv" and nxt is not None:
                mv = _try_int(nxt)
                if mv is not None and mv >= 1:
                    snap.multipv = mv
                i += 2
                continue
            if tok == "nodes" and nxt is not None:
                snap.nodes = _try_int(nxt)
                i += 2
                continue
            if tok == "nps" and nxt is not None:
                snap.nps = _try_int(nxt)
                i += 2
                continue
            if tok == "hashfull" and nxt is not None:
                snap.hashfull = _try_int(nxt)
                i += 2
                continue
            if tok == "time" and nxt is not None:
                snap.time_ms = _try_int(nxt)
                i += 2
                continue
            if tok == "score" and i + 2 < len(tokens):
                score_type = tokens[i + 1]
                score_val = tokens[i + 2]
                if score_type == "cp":
                    snap.cp = _try_int(score_val)
                elif score_type == "mate":
                    snap.mate = parse_mate_token(score_val)
                i += 3
                continue
            if tok == "pv":
                if i + 1 < len(tokens):
                    snap.pv = tokens[i + 1 :]
                    snap.move = snap.pv[0] if snap.pv else None
                break
            i += 1

        if snap.cp is None and snap.mate is not None:
            snap.cp = 30000 if snap.mate > 0 else -30000

        if snap.depth is None and snap.move is None and snap.cp is None and snap.mate is None:
            return None

        return snap


def _try_int(token: str) -> Optional[int]:
    try:
        return int(token)
    except Exception:
        return None
