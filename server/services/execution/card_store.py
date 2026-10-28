"""Stored agent cards. A card is rebuilt from the agent's log when the agent is dispatched or finishes
a run, and kept in the registry, so rendering a turn reads no logs. cards.py stays pure; the I/O is here.
"""

from __future__ import annotations

from typing import Iterable

from ...config import v2_change_enabled
from .cards import AgentCard, build_card
from .log_store import get_execution_agent_logs
from .registry import AgentRecord, get_agent_registry


def purpose_style() -> str:
    """The card purpose style of the routing profile: "sentence" under v2 (change c3), else "clip120"."""
    return "sentence" if v2_change_enabled("c3") else "clip120"


def card_from_log(record: AgentRecord) -> AgentCard:
    """Build the card from the agent's whole log."""
    entries = [(tag, text) for tag, _, text in get_execution_agent_logs().iter_entries(record.name)]
    return build_card(record, entries, purpose_style())


def card_for(record: AgentRecord) -> AgentCard:
    """The stored card; built from the log only while nothing is stored (e.g. agents seeded directly)."""
    if not (record.purpose or record.entities):
        return card_from_log(record)
    return AgentCard(
        agent_id=record.id,
        name=record.name,
        status=record.status,
        last_active_at=record.last_active_at,
        purpose=record.purpose,
        entities=tuple(record.entities),
    )


def cards_for(records: Iterable[AgentRecord]) -> list[AgentCard]:
    return [card_for(record) for record in records]


def refresh_card(agent_name: str) -> None:
    """Rebuild the agent's card from its log and store it in the registry."""
    registry = get_agent_registry()
    record = registry.find_by_name(agent_name)
    if record is not None:
        card = card_from_log(record)
        registry.update_card(record.id, card.purpose, list(card.entities))
