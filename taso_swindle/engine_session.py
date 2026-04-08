from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import SwindleConfig
from .engine_process import EngineProcess
from .info_parser import InfoParseResult, InfoParser
from .usi_messages import parse_bestmove


@dataclass
class GoOutcome:
    search_id: int
    info_result: InfoParseResult = field(default_factory=InfoParseResult)
    backend_bestmove: Optional[str] = None
    backend_ponder: Optional[str] = None
    timed_out: bool = False
    backend_dead: bool = False
    quit_requested: bool = False
    deferred_commands: list[str] = field(default_factory=list)


class EngineSession:
    """
    Search session manager.

    Reference: nnue_proxy.py:1686 main go-loop
    - per-go session boundary
    - stop/quit immediate handling
    - hard timeout + stop grace
    """

    def __init__(self, engine: EngineProcess, config: SwindleConfig) -> None:
        self.engine = engine
        self.config = config
        self._search_serial = 0

    def next_search_id(self) -> int:
        self._search_serial += 1
        return self._search_serial

    def run_go(
        self,
        go_line: str,
        stdin_reader: "QueueReadable",
        forward_engine_info: bool = False,
        on_engine_line: Optional[Callable[[str], None]] = None,
    ) -> GoOutcome:
        search_id = self.next_search_id()
        parser = InfoParser()
        result = InfoParseResult()

        outcome = GoOutcome(search_id=search_id)

        if not self.engine.alive:
            outcome.backend_dead = True
            return outcome

        self.engine.drain()
        self.engine.send(go_line)
        if not self.engine.alive:
            outcome.backend_dead = True
            return outcome

        hard_timeout = self._hard_timeout_for_go(go_line)
        start_ts = time.time()
        hard_deadline = (start_ts + hard_timeout) if hard_timeout is not None else None

        sent_stop = False
        stop_deadline: Optional[float] = None

        while True:
            while True:
                cmd = stdin_reader.get_nowait()
                if cmd is None:
                    break
                if cmd == "stop":
                    self.engine.send("stop")
                    sent_stop = True
                elif cmd == "ponderhit":
                    self.engine.send("ponderhit")
                elif cmd == "quit":
                    self.engine.send("quit")
                    outcome.quit_requested = True
                    return outcome
                else:
                    outcome.deferred_commands.append(cmd)

            now = time.time()
            if hard_deadline is not None and now > hard_deadline:
                if not sent_stop:
                    self.engine.send("stop")
                    sent_stop = True
                if stop_deadline is None:
                    stop_deadline = now + self.config.go_stop_grace_sec
                elif now > stop_deadline:
                    outcome.timed_out = True
                    break

            line = self.engine.recv(self.config.read_timeout)
            if line is None:
                if not self.engine.alive:
                    outcome.backend_dead = True
                    break
                continue

            if line.startswith("info"):
                snap = parser.parse_line(line)
                if snap is not None:
                    result.upsert(snap)
                if forward_engine_info and on_engine_line is not None:
                    on_engine_line(line)
                continue

            if line.startswith("bestmove"):
                parsed = parse_bestmove(line)
                if parsed is not None:
                    outcome.backend_bestmove = parsed.move
                    outcome.backend_ponder = parsed.ponder
                break

            if on_engine_line is not None:
                on_engine_line(line)

        outcome.info_result = result
        if not self.engine.alive and outcome.backend_bestmove is None and not outcome.quit_requested:
            outcome.backend_dead = True
        return outcome

    def _hard_timeout_for_go(self, go_line: str) -> Optional[float]:
        tokens = go_line.split()
        if "infinite" in tokens or "ponder" in tokens:
            return self.config.go_hard_sec_infinite if self.config.go_hard_sec_infinite > 0.0 else None
        return self.config.go_hard_sec if self.config.go_hard_sec > 0.0 else None


class QueueReadable:
    def get_nowait(self) -> Optional[str]:
        raise NotImplementedError
