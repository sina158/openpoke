from datetime import datetime, timezone

import pytest

from server.agents.interaction_agent import tools
from server.config import get_settings
from server.services.conversation import get_conversation_log
from server.services.execution import get_agent_registry, get_execution_agent_logs


LATER = datetime(2030, 1, 1, tzinfo=timezone.utc)


class _Loop:
    """Stands in for the running event loop: counts agent dispatches and runs nothing."""

    def __init__(self):
        self.dispatched = 0

    def create_task(self, coro):
        self.dispatched += "_execute_async" in coro.__qualname__
        coro.close()


@pytest.fixture
def loop(fresh_stores, monkeypatch):
    fake = _Loop()
    monkeypatch.setattr(tools.asyncio, "get_running_loop", lambda: fake)
    monkeypatch.setenv("OPENPOKE_PROMPT_VARIANT", "shortlist")
    get_settings.cache_clear()
    return fake


def _agent(name, request=""):
    record, _ = get_agent_registry().get_or_create(name)
    if request:
        get_execution_agent_logs().record_request(name, request)
    return record


def test_reuse_by_id_dispatches_to_that_agent(loop):
    alice = _agent("Renewal for Alice Example")

    result = tools.handle_tool_call("send_message_to_agent", {"agent_id": alice.id, "instructions": "chase the form"})

    assert result.success and result.payload["agent_id"] == alice.id
    assert result.payload["new_agent_created"] is False and loop.dispatched == 1
    requests = [text for tag, _, text in get_execution_agent_logs().iter_entries(alice.name) if tag == "agent_request"]
    assert requests == ["chase the form"]


def test_unknown_id_is_an_error_the_model_sees(loop):
    result = tools.send_message_to_agent("go", agent_id="a_9999")
    assert not result.success and "Unknown agent_id" in result.payload["error"]
    assert loop.dispatched == 0 and get_agent_registry().list() == []


def test_an_existing_name_reuses_instead_of_creating(loop):
    alice = _agent("Renewal for Alice Example")
    result = tools.send_message_to_agent("go", agent_name="renewal for alice example!")
    assert result.payload["agent_id"] == alice.id and len(get_agent_registry().list()) == 1


def test_duplicate_guard_stops_a_near_identical_name_until_confirmed(loop):
    alice = _agent("Renewal for Alice Example")

    held = tools.send_message_to_agent("go", agent_name="Alice Example renewal")
    assert not held.success and held.payload["status"] == "possible_duplicates"
    assert held.payload["possible_duplicates"] == [{"agent_id": alice.id, "name": alice.name, "status": "active"}]
    assert loop.dispatched == 0 and len(get_agent_registry().list()) == 1

    created = tools.send_message_to_agent("go", agent_name="Alice Example renewal", confirm_new=True)
    assert created.success and created.payload["new_agent_created"] is True
    assert len(get_agent_registry().list()) == 2


@pytest.mark.parametrize(
    "existing, new",
    [
        ("Smith Plumbing COI", "Smith Plumbing & Heating COI"),  # a different business
        ("Auto claim for Bob Example", "Auto endorsement for Bob Example"),  # same client, other matter
    ],
)
def test_duplicate_guard_does_not_merge_different_matters(loop, existing, new):
    _agent(existing)
    result = tools.send_message_to_agent("go", agent_name=new)
    assert result.success and result.payload["new_agent_created"] is True


def test_upstream_prompt_variant_never_runs_the_guard(loop, monkeypatch):
    _agent("Renewal for Alice Example")
    monkeypatch.setenv("OPENPOKE_PROMPT_VARIANT", "original")
    get_settings.cache_clear()
    result = tools.send_message_to_agent("go", agent_name="Alice Example renewal")
    assert result.success and result.payload["new_agent_created"] is True


def test_name_similarity():
    assert tools.name_similarity("Renewal for Alice Example", "alice example RENEWAL") == 1.0
    assert tools.name_similarity("Smith Plumbing COI", "Smith Plumbing & Heating COI") == 0.75
    assert tools.name_similarity("", "anything") == 0.0


def test_reusing_an_archived_agent_revives_it(loop):
    old = _agent("Claim for Dana Example")
    get_agent_registry().archive_stale(LATER, idle_days=30, protected_ids=set())
    assert get_agent_registry().get(old.id).status == "archived"

    tools.send_message_to_agent("any update?", agent_id=old.id)

    assert get_agent_registry().get(old.id).status == "active"


def test_clarify_asks_the_user_and_dispatches_nothing(loop):
    a, b = _agent("COI for Example Ltd site A"), _agent("COI for Example Ltd site B")

    result = tools.handle_tool_call(
        "clarify_agent_choice", {"candidate_ids": [a.id, b.id], "question": "Which site's certificate?"}
    )

    assert result.success and result.user_message == "Which site's certificate?"
    assert result.payload == {"status": "asked", "candidate_ids": [a.id, b.id]}
    assert loop.dispatched == 0
    assert [tag for tag, _, _ in get_conversation_log().iter_entries()] == ["poke_reply"]

    bad = tools.clarify_agent_choice(["a_9999"], "Which one?")
    assert not bad.success and "a_9999" in bad.payload["error"]


def test_search_agents_reaches_archived_agents_by_email(loop):
    _agent("Quote for Example Ltd", "Quote commercial property for ops@example-ltd.test")
    old = _agent("Claim for Dana Example", "Open an auto claim for dana@example.ca")
    get_agent_registry().archive_stale(LATER, idle_days=30, protected_ids=set())

    result = tools.handle_tool_call("search_agents", {"query": "dana@example.ca"})

    assert result.success
    assert result.payload["agents"][0]["agent_id"] == old.id
    assert result.payload["agents"][0]["status"] == "archived"
    assert tools.search_agents("unrelated words").payload["agents"] == []
