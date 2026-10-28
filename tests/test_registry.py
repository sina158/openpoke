import json
from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.registry import (
    AgentRegistry,
    get_agent_registry,
    reset_agent_registry,
    slugify,
)
from server.services.execution.roster import AgentRoster

NOW = datetime(2026, 1, 31, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def registry(tmp_path):
    return AgentRegistry(tmp_path / "openpoke.db")


def test_get_or_create_mints_sequential_ids_and_reuses_by_slug(registry):
    alice, created = registry.get_or_create("Email to Alice")
    assert created and alice.id == "a_0001" and alice.slug == "email-to-alice"
    assert alice.status == "active" and alice.run_count == 0 and alice.entities == []

    bob, created = registry.get_or_create("Renewal for Bob")
    assert created and bob.id == "a_0002"

    again, created = registry.get_or_create("email to alice!")
    assert not created and again.id == "a_0001" and again.name == "Email to Alice"


def test_get_find_and_list(registry):
    first, _ = registry.get_or_create("First")
    second, _ = registry.get_or_create("Second")

    assert registry.get(first.id) == first
    assert registry.get("a_9999") is None
    assert registry.find_by_name("  SECOND ") == second
    assert registry.find_by_name("Third") is None
    assert [r.id for r in registry.list()] == [first.id, second.id]


def test_touch_and_update_card(registry):
    agent, _ = registry.get_or_create("Quote for Example Ltd")
    registry.touch(agent.id, now=NOW)
    registry.touch(agent.id, now=NOW + timedelta(hours=1))
    registry.update_card(agent.id, "Quote for a commercial policy", ["ops@example.com", "Example Ltd"])

    stored = registry.get(agent.id)
    assert stored.run_count == 2
    assert stored.last_active_at == "2026-01-31T13:00:00+00:00"
    assert stored.purpose == "Quote for a commercial policy"
    assert stored.entities == ["ops@example.com", "Example Ltd"]

    with pytest.raises(KeyError):
        registry.touch("a_9999")


def test_slug_collision_cannot_split_one_agent_or_merge_two_logs(registry, tmp_path):
    names = ["Email Alice", "email-alice", "EMAIL  ALICE!", "Email Bob"]
    ids = [registry.get_or_create(name)[0].id for name in names]
    assert ids == ["a_0001", "a_0001", "a_0001", "a_0002"]
    assert [r.name for r in registry.list()] == ["Email Alice", "Email Bob"]

    logs = ExecutionAgentLogStore(tmp_path / "logs")
    for name in names:
        logs.record_request(name, f"request via {name}")
    files = {path.name for path in (tmp_path / "logs").glob("*.log")}
    assert files == {f"{record.slug}.log" for record in registry.list()}


def test_archive_stale_skips_recent_and_protected(registry):
    old, _ = registry.get_or_create("Old matter")
    kept, _ = registry.get_or_create("Old but protected")
    fresh, _ = registry.get_or_create("Fresh matter")
    registry.touch(old.id, now=NOW - timedelta(days=40))
    registry.touch(kept.id, now=NOW - timedelta(days=40))
    registry.touch(fresh.id, now=NOW - timedelta(days=5))

    archived = registry.archive_stale(NOW, idle_days=30, protected_ids={kept.id})

    assert archived == [old.id]
    assert registry.get(old.id).status == "archived"
    assert registry.get(kept.id).status == "active"
    assert [r.id for r in registry.list()] == [kept.id, fresh.id]
    assert [r.id for r in registry.list(status=None)] == [old.id, kept.id, fresh.id]
    assert registry.archive_stale(NOW, idle_days=30, protected_ids={kept.id}) == []


def test_revive_restarts_the_idle_clock(registry):
    agent, _ = registry.get_or_create("Claim follow-up")
    registry.touch(agent.id, now=NOW - timedelta(days=60))
    registry.archive_stale(NOW, idle_days=30, protected_ids=set())

    registry.revive(agent.id, now=NOW)

    assert registry.get(agent.id).status == "active"
    assert registry.archive_stale(NOW, idle_days=30, protected_ids=set()) == []


def test_clear_never_reuses_ids(registry):
    registry.get_or_create("One")
    registry.get_or_create("Two")
    registry.clear()
    assert registry.list(status=None) == []
    assert registry.get_or_create("Three")[0].id == "a_0003"


def test_migrate_from_roster_json_is_idempotent(registry, tmp_path):
    roster_json = tmp_path / "roster.json"
    roster_json.write_text(json.dumps(["Email Alice", "Renewal Bob", "email alice", ""]), encoding="utf-8")

    assert registry.migrate_from_roster_json(roster_json) == 2
    assert registry.migrate_from_roster_json(roster_json) == 0
    assert [r.name for r in registry.list()] == ["Email Alice", "Renewal Bob"]
    assert registry.migrate_from_roster_json(tmp_path / "missing.json") == 0


def test_singleton_migrates_roster_json_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path))
    roster_json = tmp_path / "execution_agents" / "roster.json"
    roster_json.parent.mkdir()
    roster_json.write_text(json.dumps(["Email Alice"]), encoding="utf-8")
    reset_agent_registry()
    try:
        registry = get_agent_registry()
        assert get_agent_registry() is registry
        assert [r.name for r in registry.list()] == ["Email Alice"]
        assert not roster_json.exists()
        assert roster_json.with_name("roster.json.migrated").exists()
        assert (tmp_path / "openpoke.db").exists()
    finally:
        reset_agent_registry()


def test_roster_shim_keeps_the_old_api(registry):
    roster = AgentRoster(registry)
    roster.load()
    roster.add_agent("Email Alice")
    roster.add_agent("Renewal Bob")
    roster.add_agent("email alice")
    assert roster.get_agents() == ["Email Alice", "Renewal Bob"]

    alice = registry.find_by_name("Email Alice")
    registry.touch(alice.id, now=NOW - timedelta(days=90))
    registry.archive_stale(NOW, idle_days=30, protected_ids=set())
    assert roster.get_agents() == ["Renewal Bob"]

    roster.add_agent("Email Alice")
    assert roster.get_agents() == ["Email Alice", "Renewal Bob"]

    roster.clear()
    assert roster.get_agents() == []
