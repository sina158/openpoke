"""Where the current interaction turn came from.

Only a turn the user typed may approve an outbound action. The default is ``None``, so a code path
that forgets to set provenance can never approve anything.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional, Tuple

PROVENANCES = ("user", "agent", "email", "automation")

_CURRENT: ContextVar[Optional[Tuple[str, str]]] = ContextVar("turn_provenance", default=None)


@contextmanager
def turn_provenance(provenance: str) -> Iterator[str]:
    """Run the block as one turn from ``provenance``; yields a fresh turn id."""
    if provenance not in PROVENANCES:
        raise ValueError(f"unknown provenance: {provenance!r}")
    turn_id = uuid.uuid4().hex
    token = _CURRENT.set((provenance, turn_id))
    try:
        yield turn_id
    finally:
        _CURRENT.reset(token)


def current_provenance() -> Optional[str]:
    """Return the provenance of the turn being handled, or ``None`` outside any turn."""
    current = _CURRENT.get()
    return current[0] if current else None


def current_turn_id() -> Optional[str]:
    """Return the id of the turn being handled, or ``None`` outside any turn."""
    current = _CURRENT.get()
    return current[1] if current else None


__all__ = ["PROVENANCES", "current_provenance", "current_turn_id", "turn_provenance"]
