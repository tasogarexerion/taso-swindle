from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Reference: nnue_proxy.py:320 parse_mate_token
# Reference: nnue_proxy.py:327 is_special_bestmove
# Reference: nnue_proxy.py:331 is_usi_move_token
# Reference: nnue_proxy.py:1668 _parse_option_name

def _try_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def parse_mate_token(token: Optional[str]) -> Optional[int]:
    if token is None:
        return None
    text = token.strip()
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    return _try_int(text)


def is_special_bestmove(token: str) -> bool:
    normalized = token.strip().lower()
    return normalized in {"resign", "win", "0000", "(none)"}


def is_usi_move_token(token: str) -> bool:
    text = token.strip()
    if not text or is_special_bestmove(text):
        return False

    if "*" in text:
        if len(text) != 4:
            return False
        piece = text[0].upper()
        if piece not in {"P", "L", "N", "S", "G", "B", "R", "K"}:
            return False
        if text[1] != "*":
            return False
        to_file = _try_int(text[2])
        to_rank = ord(text[3]) - ord("a") + 1
        return to_file is not None and 1 <= to_file <= 9 and 1 <= to_rank <= 9

    promote = text.endswith("+")
    core = text[:-1] if promote else text
    if len(core) != 4:
        return False

    f1 = _try_int(core[0])
    r1 = ord(core[1]) - ord("a") + 1
    f2 = _try_int(core[2])
    r2 = ord(core[3]) - ord("a") + 1
    if f1 is None or f2 is None:
        return False
    return 1 <= f1 <= 9 and 1 <= r1 <= 9 and 1 <= f2 <= 9 and 1 <= r2 <= 9


@dataclass(frozen=True)
class SetOptionCommand:
    name: str
    value: str


def parse_setoption(line: str) -> Optional[SetOptionCommand]:
    if not line.startswith("setoption"):
        return None

    tokens = line.split()
    if "name" not in tokens:
        return None

    i_name = tokens.index("name") + 1
    if i_name >= len(tokens):
        return None

    if "value" in tokens:
        i_value = tokens.index("value")
        name = " ".join(tokens[i_name:i_value]).strip()
        value = " ".join(tokens[i_value + 1 :]).strip()
    else:
        name = " ".join(tokens[i_name:]).strip()
        value = ""

    if not name:
        return None

    return SetOptionCommand(name=name, value=value)


def parse_option_name(option_line: str) -> Optional[str]:
    if not option_line.startswith("option "):
        return None

    tokens = option_line.split()
    if "name" not in tokens:
        return None

    i_name = tokens.index("name") + 1
    if i_name >= len(tokens):
        return None

    i_type = tokens.index("type") if "type" in tokens and tokens.index("type") > i_name else len(tokens)
    name = " ".join(tokens[i_name:i_type]).strip()
    return name or None


@dataclass(frozen=True)
class BestMove:
    move: str
    ponder: Optional[str]


def parse_bestmove(line: str) -> Optional[BestMove]:
    if not line.startswith("bestmove"):
        return None
    tokens = line.split()
    if len(tokens) < 2:
        return None
    move = tokens[1]
    ponder: Optional[str] = None
    if "ponder" in tokens:
        i = tokens.index("ponder")
        if i + 1 < len(tokens):
            ponder = tokens[i + 1]
    return BestMove(move=move, ponder=ponder)
