from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SwindleContext:
    side_to_move: str
    root_sfen: str
    root_position_cmd: str
    root_eval_cp: Optional[int]
    root_mate_score: Optional[int]
    is_losing: bool
    is_lost: bool
    time_left_ms: Optional[int]
    byoyomi_ms: Optional[int]
    increment_ms: Optional[int]
    mode: str
    swindle_enabled: bool
    emergency_fast_mode: bool
    dynamic_drop_cap_cp: int
