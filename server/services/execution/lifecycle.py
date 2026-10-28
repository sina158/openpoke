"""Archive idle agents so the roster stays small over time (plan 2.1 A.4).

Only the shortlist roster has a lifecycle; the full modes (the upstream behaviour and its ablations)
never archive. ``on_turn_start`` is the one hook run before every turn: the runtime calls it, and so
does the routing eval per case, so the eval measures what ships.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ...config import get_settings
from ...utils.clock import now_utc
from ...utils.paths import data_dir
from ..approvals import get_approval_store
from ..triggers import get_trigger_service
from .registry import AgentRegistry, get_agent_registry, slugify

ARCHIVE_EVERY = timedelta(hours=1)
# Keyed by data dir, so each store (an eval case, a test) keeps its own hourly throttle.
_last_archive: dict[Path, datetime] = {}


def protected_agent_ids(registry: AgentRegistry) -> set[str]:
    """Agents that own an active trigger or a pending action; archiving never touches them."""
    ids = {action.agent_id for action in get_approval_store().list_pending() if action.agent_id}
    trigger_slugs = {slugify(name) for name in get_trigger_service().active_agent_names()}
    ids.update(record.id for record in registry.list("active") if record.slug in trigger_slugs)
    return ids


def archive_idle_agents(now: Optional[datetime] = None) -> list[str]:
    """Archive agents idle longer than ``OPENPOKE_ARCHIVE_IDLE_DAYS``, at most once an hour.

    Does nothing unless ``OPENPOKE_ROSTER_MODE`` is shortlist.
    """
    if get_settings().roster_mode != "shortlist":
        return []
    now = now or now_utc()
    key = data_dir()
    last = _last_archive.get(key)
    if last is not None and timedelta(0) <= now - last < ARCHIVE_EVERY:
        return []
    _last_archive[key] = now
    registry = get_agent_registry()
    return registry.archive_stale(now, get_settings().archive_idle_days, protected_agent_ids(registry))


def on_turn_start(now: Optional[datetime] = None) -> list[str]:
    """Run before every interaction turn; returns the ids it archived."""
    return archive_idle_agents(now)


def reset_archive_clock() -> None:
    """Forget when archiving last ran (tests and simulations)."""
    _last_archive.clear()
