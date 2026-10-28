"""Archive idle agents so the roster stays small over time (plan 2.1 A.4)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ...config import get_settings
from ...utils.clock import now_utc
from ..approvals import get_approval_store
from ..triggers import get_trigger_service
from .registry import AgentRegistry, get_agent_registry, slugify

ARCHIVE_EVERY = timedelta(hours=1)
_last_archive: Optional[datetime] = None


def protected_agent_ids(registry: AgentRegistry) -> set[str]:
    """Agents that own an active trigger or a pending action; archiving never touches them."""
    ids = {action.agent_id for action in get_approval_store().list_pending() if action.agent_id}
    trigger_slugs = {slugify(name) for name in get_trigger_service().active_agent_names()}
    ids.update(record.id for record in registry.list("active") if record.slug in trigger_slugs)
    return ids


def archive_idle_agents(now: Optional[datetime] = None) -> list[str]:
    """Archive agents idle longer than ``OPENPOKE_ARCHIVE_IDLE_DAYS``; runs at most once an hour."""
    global _last_archive
    now = now or now_utc()
    if _last_archive is not None and timedelta(0) <= now - _last_archive < ARCHIVE_EVERY:
        return []
    _last_archive = now
    registry = get_agent_registry()
    return registry.archive_stale(now, get_settings().archive_idle_days, protected_agent_ids(registry))


def reset_archive_clock() -> None:
    """Forget when archiving last ran (tests and simulations)."""
    global _last_archive
    _last_archive = None
