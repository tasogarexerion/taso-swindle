from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class PositionState:
    raw_position: str = "position startpos"
    root_sfen: str = "startpos"
    moves: list[str] = field(default_factory=list)
    game_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ply: int = 0
    last_updated_ts: float = field(default_factory=time.time)

    def on_new_game(self) -> None:
        self.raw_position = "position startpos"
        self.root_sfen = "startpos"
        self.moves = []
        self.ply = 0
        self.game_id = uuid.uuid4().hex
        self.last_updated_ts = time.time()

    def update_from_command(self, command: str) -> None:
        if not command.startswith("position"):
            return

        self.raw_position = command
        self.last_updated_ts = time.time()

        tokens = command.split()
        if "startpos" in tokens:
            self.root_sfen = "startpos"
        elif "sfen" in tokens:
            i = tokens.index("sfen")
            j = tokens.index("moves") if "moves" in tokens and tokens.index("moves") > i else len(tokens)
            self.root_sfen = " ".join(tokens[i + 1 : j]).strip() or "startpos"

        if "moves" in tokens:
            i_moves = tokens.index("moves")
            self.moves = tokens[i_moves + 1 :]
        else:
            self.moves = []

        self.ply = len(self.moves)

    def side_to_move(self) -> str:
        if self.root_sfen == "startpos":
            # startpos starts from black to move.
            return "b" if self.ply % 2 == 0 else "w"

        toks = self.root_sfen.split()
        if len(toks) >= 2:
            root_side = toks[1]
        else:
            root_side = "b"

        if self.ply % 2 == 0:
            return root_side
        return "w" if root_side == "b" else "b"

    def command_with_move(self, move: str) -> str:
        if "moves" in self.raw_position.split():
            return f"{self.raw_position} {move}"
        return f"{self.raw_position} moves {move}"
