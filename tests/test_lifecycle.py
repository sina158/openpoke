import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.config import get_settings
from server.services.approvals import get_approval_store
from server.services.execution import get_agent_registry, get_execution_agent_logs, lifecycle

NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def agents(fresh_stores, monkeypatch):
    """Four agents last active 60 days ago; one owns a trigger, one a pending send."""
    monkeypatch.setattr(lifecycle, "get_trigger_service", lambda: SimpleNamespace(active_agent_names=lambda: ["Weekly reminder"]))
    lifecycle.reset_archive_clock()
    registry = get_agent_registry()
    records = {}
    for name in ("Idle quote", "Weekly reminder", "Reply awaiting approval", "Fresh claim"):
        record, _ = registry.get_or_create(name)
        registry.touch(record.id, now=NOW - timedelta(days=60 if name != "Fresh claim" else 2))
        records[name] = record
    get_approval_store().propose("GMAIL_REPLY_TO_THREAD", {"thread_id": "t_1"}, agent_id=records["Reply awaiting approval"].id)
    yield records
    lifecycle.reset_archive_clock()


def test_archive_skips_agents_with_a_trigger_or_a_pending_action(agents):
    archived = lifecycle.archive_idle_agents(NOW)

    assert archived == [agents["Idle quote"].id]
    statuses = {r.name: r.status for r in get_agent_registry().list(status=None)}
    assert statuses == {
        "Idle quote": "archived",
        "Weekly reminder": "active",
        "Reply awaiting approval": "active",
        "Fresh claim": "active",
    }


def test_archive_runs_at_most_once_an_hour(agents):
    assert lifecycle.archive_idle_agents(NOW) == [agents["Idle quote"].id]
    fresh = agents["Fresh claim"].id
    get_agent_registry().touch(fresh, now=NOW - timedelta(days=40))

    assert lifecycle.archive_idle_agents(NOW + timedelta(minutes=30)) == []
    assert lifecycle.archive_idle_agents(NOW + timedelta(minutes=61)) == [fresh]


def test_a_run_revives_counts_and_refreshes_the_card(agents, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EXECUTION_STUB", "1")
    monkeypatch.setenv("OPENPOKE_FIXED_NOW", NOW.isoformat())
    get_settings.cache_clear()
    lifecycle.archive_idle_agents(NOW)
    idle = agents["Idle quote"]
    assert get_agent_registry().get(idle.id).status == "archived"

    async def no_dispatch(self, payload):
        return None

    monkeypatch.setattr(ExecutionBatchManager, "_dispatch_to_interaction_agent", no_dispatch)
    instructions = "Send the quote for Example Ltd to ops@example-ltd.test"
    get_execution_agent_logs().record_request(idle.name, instructions)
    result = asyncio.run(ExecutionBatchManager(provenance="automation").execute_agent(idle.name, instructions))

    assert result.success and result.response == f"Done: {instructions}"
    record = get_agent_registry().get(idle.id)
    assert record.status == "active" and record.run_count == 2
    assert record.last_active_at == "2026-10-27T09:00:00+00:00"
    assert record.purpose.startswith("Send the quote for Example Ltd")
    assert "ops@example-ltd.test" in record.entities


def test_each_turn_starts_by_archiving(fresh_stores, monkeypatch):
    from server.agents.interaction_agent import runtime as interaction

    monkeypatch.setenv("OPENPOKE_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    calls = []
    monkeypatch.setattr(interaction, "on_turn_start", lambda: calls.append("archive") or [])

    async def no_loop(self, system_prompt, messages):
        return interaction._LoopSummary(last_assistant_text="ok")

    monkeypatch.setattr(interaction.InteractionAgentRuntime, "_run_interaction_loop", no_loop)
    runtime = interaction.InteractionAgentRuntime()
    asyncio.run(runtime.execute("hello"))
    asyncio.run(runtime.handle_agent_message("[SUCCESS] Some agent: done"))
    assert calls == ["archive", "archive"]


def test_full_roster_modes_have_no_lifecycle(agents, monkeypatch):
    for mode in ("full", "full_cards"):
        monkeypatch.setenv("OPENPOKE_ROSTER_MODE", mode)
        get_settings.cache_clear()
        assert lifecycle.on_turn_start(NOW) == []
    assert all(r.status == "active" for r in get_agent_registry().list(status=None))

    monkeypatch.setenv("OPENPOKE_ROSTER_MODE", "shortlist")
    get_settings.cache_clear()
    assert lifecycle.on_turn_start(NOW) == [agents["Idle quote"].id]


def test_each_data_dir_has_its_own_throttle(agents, tmp_path, monkeypatch):
    assert lifecycle.on_turn_start(NOW) == [agents["Idle quote"].id]

    from server.services.execution.registry import reset_agent_registry

    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path / "other"))
    reset_agent_registry()
    other, _ = get_agent_registry().get_or_create("Old matter elsewhere")
    get_agent_registry().touch(other.id, now=NOW - timedelta(days=90))
    assert lifecycle.on_turn_start(NOW) == [other.id]


def test_interaction_calls_carry_the_turn_id(fresh_stores, monkeypatch):
    from server.agents.interaction_agent import runtime as interaction
    from server.services.provenance import turn_provenance

    monkeypatch.setenv("OPENPOKE_LLM_API_KEY", "test-key")
    get_settings.cache_clear()
    seen = {}

    async def fake_client(**kwargs):
        seen.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(interaction, "request_chat_completion", fake_client)
    runtime = interaction.InteractionAgentRuntime()
    with turn_provenance("user") as turn_id:
        asyncio.run(runtime._make_llm_call("system", [{"role": "user", "content": "hi"}]))
    assert seen["turn_id"] == turn_id and seen["role"] == "interaction"
