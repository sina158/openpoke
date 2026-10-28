"""Cards for registered agents, read from their logs. The I/O lives here so cards.py stays pure."""

from __future__ import annotations

from typing import Iterable

from .cards import AgentCard, build_card
from .log_store import get_execution_agent_logs
from .registry import AgentRecord, get_agent_registry


def card_for(record: AgentRecord) -> AgentCard:
    """Build the card from the agent's log; with no log it falls back to the stored purpose and entities."""
    entries = [(tag, text) for tag, _, text in get_execution_agent_logs().iter_entries(record.name)]
    return build_card(record, entries)


def cards_for(records: Iterable[AgentRecord]) -> list[AgentCard]:
    return [card_for(record) for record in records]


def refresh_card(agent_name: str) -> None:
    """Store the card built from the agent's log in the registry (after each run)."""
    registry = get_agent_registry()
    record = registry.find_by_name(agent_name)
    if record is not None:
        card = card_for(record)
        registry.update_card(record.id, card.purpose, list(card.entities))
