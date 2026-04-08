"""Swindle evaluation package."""

from .candidate import CandidateMove, SwindleFeatures
from .context import SwindleContext

__all__ = [
    "CandidateMove",
    "SwindleFeatures",
    "SwindleContext",
    "Stage1Decision",
    "SwindleController",
]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name == "Stage1Decision":
        from .controller import Stage1Decision

        return Stage1Decision
    if name == "SwindleController":
        from .controller import SwindleController

        return SwindleController
    raise AttributeError(name)
