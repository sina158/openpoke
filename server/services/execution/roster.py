"""Names-only view of the agent registry, kept so existing callers work unchanged."""

from __future__ import annotations

from typing import Optional

from .registry import AgentRegistry, get_agent_registry


class AgentRoster:
    """The old roster API (a list of active agent names) backed by the SQLite registry."""

    def __init__(self, registry: Optional[AgentRegistry] = None) -> None:
        self._registry = registry

    def _store(self) -> AgentRegistry:
        return self._registry or get_agent_registry()

    def load(self) -> None:
        """No-op: the registry is read on every call."""

    def add_agent(self, agent_name: str) -> None:
        """Register an agent by name; reusing an archived agent's name revives it."""
        record, _ = self._store().get_or_create(agent_name)
        if record.status == "archived":
            self._store().revive(record.id)

    def get_agents(self) -> list[str]:
        """Names of the active agents in creation order."""
        return [record.name for record in self._store().list("active")]

    def clear(self) -> None:
        self._store().clear()


_agent_roster = AgentRoster()


def get_agent_roster() -> AgentRoster:
    """Get the singleton roster instance."""
    return _agent_roster
