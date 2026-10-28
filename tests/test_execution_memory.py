"""OPENPOKE_EXECUTION_MEMORY: an execution agent's history, either the whole log or recent plus retrieved."""

import asyncio

import pytest

from server.agents.execution_agent.agent import ExecutionAgent, retrieve_entries
from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.config import get_settings
from server.services.execution import get_execution_agent_logs

AGENT = "Claim for Ivy Example"
MATTER = "Open a water damage claim for Ivy Example, home policy HO-4410-27, burst pipe at 12 Elm Road."
REQUEST = "The adjuster wants photos of the burst pipe for HO-4410-27."
HEADER = "# Execution History\n\n"


@pytest.fixture
def long_log(fresh_stores, monkeypatch):
    """200 entries: the matter is named once, early; the rest is routine, a few mention the adjuster."""
    monkeypatch.delenv("OPENPOKE_EXECUTION_MEMORY", raising=False)
    logs = get_execution_agent_logs()
    for i in range(200):
        if i == 4:
            logs.record_request(AGENT, MATTER)
        elif i % 50 == 10:
            logs.record_agent_response(AGENT, "Sent the adjuster the signed renewal form.")
        elif i % 2:
            logs.record_agent_response(AGENT, "Checked the inbox; nothing new from the insurer today.")
        else:
            logs.record_request(AGENT, "Any news on this file?")
    return logs


def _history(monkeypatch, mode, instructions=REQUEST):
    if mode is not None:
        monkeypatch.setenv("OPENPOKE_EXECUTION_MEMORY", mode)
    prompt = ExecutionAgent(AGENT).build_system_prompt_with_history(instructions)
    return prompt.split(HEADER, 1)[1]


def test_recent_is_the_default_and_keeps_the_whole_log(long_log, monkeypatch):
    whole = long_log.load_transcript(AGENT)
    assert _history(monkeypatch, None) == whole
    assert _history(monkeypatch, "recent") == whole
    assert _history(monkeypatch, "recent", instructions="anything else") == whole


def test_retrieved_is_bounded_and_keeps_the_old_entry_naming_the_matter(long_log, monkeypatch):
    recent = _history(monkeypatch, "recent")
    retrieved = _history(monkeypatch, "retrieved")
    lines = retrieved.split("\n")
    entries = list(long_log.iter_entries(AGENT))
    measured = f"history block tokens (chars / 4): recent={len(recent) / 4:.0f}, retrieved={len(retrieved) / 4:.0f}"

    assert lines[0] == "8 of 200 entries: the latest and those most relevant to this request.", measured
    assert "HO-4410-27" in lines[1] and lines[1].startswith("<agent_request"), measured
    assert lines[-3:] == recent.split("\n")[-3:], measured
    assert len(retrieved) < len(recent) / 10, measured
    assert len(entries) == 200


def test_retrieve_entries_keeps_log_order_without_duplicates():
    entries = [("agent_response", str(i), "Routine check, nothing new.") for i in range(30)]
    entries[2] = ("agent_request", "2", "Open a claim for the burst pipe.")
    entries[28] = ("agent_response", "28", "Asked for photos of the burst pipe.")  # recent and relevant
    kept = retrieve_entries(entries, "photos of the burst pipe")
    assert [timestamp for _, timestamp, _ in kept] == ["2", "27", "28", "29"]


def test_a_short_log_is_kept_whole(fresh_stores, monkeypatch):
    get_execution_agent_logs().record_request(AGENT, MATTER)
    whole = get_execution_agent_logs().load_transcript(AGENT)
    assert _history(monkeypatch, "retrieved") == "1 of 1 entries: the latest and those most relevant to this request.\n" + whole


def test_an_unknown_mode_fails_loudly(long_log, monkeypatch):
    with pytest.raises(ValueError, match="OPENPOKE_EXECUTION_MEMORY"):
        _history(monkeypatch, "everything")


def test_the_runtime_retrieves_against_the_instructions_it_was_given(long_log, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EXECUTION_MEMORY", "retrieved")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    runtime = ExecutionAgentRuntime(AGENT)
    seen = []

    async def fake_call(system_prompt, messages, with_tools=True):
        seen.append(system_prompt)
        return {"choices": [{"message": {"role": "assistant", "content": "Photos requested."}}]}

    runtime._make_llm_call = fake_call
    result = asyncio.run(runtime.execute(REQUEST))

    assert result.success and len(seen) == 1
    assert MATTER in seen[0] and "8 of 200 entries" in seen[0]
