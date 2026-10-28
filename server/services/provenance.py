"""Where the current interaction turn came from, and what it said.

Only a turn the user typed may approve an outbound action. The default is ``None``, so a code path
that forgets to set provenance can never approve anything. The turn text is the input that started
the turn (for a user turn, exactly what the user typed), never model output.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, NamedTuple, Optional

PROVENANCES = ("user", "agent", "email", "automation")


class _Turn(NamedTuple):
    provenance: str
    turn_id: str
    text: Optional[str]


_CURRENT: ContextVar[Optional[_Turn]] = ContextVar("turn_provenance", default=None)


@contextmanager
def turn_provenance(provenance: str, text: Optional[str] = None) -> Iterator[str]:
    """Run the block as one turn from ``provenance`` started by ``text``; yields a fresh turn id."""
    if provenance not in PROVENANCES:
        raise ValueError(f"unknown provenance: {provenance!r}")
    turn_id = uuid.uuid4().hex
    token = _CURRENT.set(_Turn(provenance, turn_id, text))
    try:
        yield turn_id
    finally:
        _CURRENT.reset(token)


def current_provenance() -> Optional[str]:
    """Return the provenance of the turn being handled, or ``None`` outside any turn."""
    current = _CURRENT.get()
    return current.provenance if current else None


def current_turn_id() -> Optional[str]:
    """Return the id of the turn being handled, or ``None`` outside any turn."""
    current = _CURRENT.get()
    return current.turn_id if current else None


def current_turn_text() -> Optional[str]:
    """Return the text that started the turn being handled, or ``None`` outside any turn."""
    current = _CURRENT.get()
    return current.text if current else None


__all__ = [
    "PROVENANCES",
    "current_provenance",
    "current_turn_id",
    "current_turn_text",
    "turn_provenance",
]
