"""The arm flags really switch the rendering, the tool schemas and the prompt (plan 4.3 wiring test)."""

import re

import pytest

from server.agents.interaction_agent.agent import build_system_prompt, prepare_message_with_history
from server.agents.interaction_agent.tools import get_tool_schemas
from server.config import get_settings
from server.services.execution import get_agent_registry, get_execution_agent_logs

ARMS = {
    "baseline": ("full", "original"),
    "prompt_only": ("full", "shortlist"),
    "fix": ("shortlist", "shortlist"),
}
NAMES = [f"Renewal for Client {n:02d}" for n in range(12)]


@pytest.fixture
def seeded(fresh_stores, monkeypatch):
    monkeypatch.setenv("OPENPOKE_FIXED_NOW", "2026-10-27T09:00:00Z")
    registry, logs = get_agent_registry(), get_execution_agent_logs()
    for name in NAMES:
        registry.get_or_create(name)
        logs.record_request(name, f"Handle the home renewal for {name[12:]} at client{name[-2:]}@example.com")

    def arm(name):
        roster_mode, variant = ARMS[name]
        monkeypatch.setenv("OPENPOKE_ROSTER_MODE", roster_mode)
        monkeypatch.setenv("OPENPOKE_PROMPT_VARIANT", variant)
        get_settings.cache_clear()
        content = prepare_message_with_history("renew client 03 please", "")[0]["content"]
        return content, get_tool_schemas(), build_system_prompt()

    return arm


def _send_schema(schemas):
    return next(s["function"]["parameters"] for s in schemas if s["function"]["name"] == "send_message_to_agent")


def test_baseline_is_the_upstream_rendering_and_tools(seeded):
    content, schemas, prompt = seeded("baseline")

    upstream_block = "<active_agents>\n" + "\n".join(f'<agent name="{n}" />' for n in NAMES) + "\n</active_agents>"
    assert upstream_block in content
    assert "a_0001" not in content and "mode=" not in content
    assert [s["function"]["name"] for s in schemas] == [
        "send_message_to_agent", "send_message_to_user", "send_draft", "wait", "confirm_action",
    ]
    send = _send_schema(schemas)
    assert set(send["properties"]) == {"agent_name", "instructions"}
    assert send["required"] == ["agent_name", "instructions"]
    assert "Don't worry if the agent name is unrelated" in prompt


def test_prompt_only_keeps_the_full_roster_but_switches_prompt_and_tools(seeded):
    content, schemas, prompt = seeded("prompt_only")
    baseline_content, _, _ = seeded("baseline")

    assert content == baseline_content
    names = [s["function"]["name"] for s in schemas]
    assert "clarify_agent_choice" in names and "search_agents" not in names
    send = _send_schema(schemas)
    assert set(send["properties"]) == {"instructions", "agent_id", "agent_name", "confirm_new"}
    assert send["required"] == ["instructions"]
    assert "clarify_agent_choice" in prompt


def test_fix_renders_a_shortlist_with_ids_and_offers_search(seeded):
    content, schemas, prompt = seeded("fix")

    assert '<active_agents mode="shortlist" shown="8" total="12">' in content
    lines = re.findall(r"<agent id=\"(a_\d+)\" name=\"([^\"]+)\"", content)
    assert len(lines) == 8
    assert lines[0] == ("a_0004", "Renewal for Client 03")
    assert "4 more agents are not shown" in content and "search_agents(query)" in content
    assert 'last_active="0h ago"' in content
    assert "search_agents" in [s["function"]["name"] for s in schemas]
    assert prompt == build_system_prompt("shortlist")


def test_fix_rendering_is_byte_stable_under_a_fixed_clock(seeded):
    first, _, _ = seeded("fix")
    second, _, _ = seeded("fix")
    assert first == second


@pytest.mark.parametrize("arm", sorted(ARMS))
def test_every_schema_declares_what_it_requires(seeded, arm):
    _, schemas, _ = seeded(arm)
    for schema in schemas:
        parameters = schema["function"]["parameters"]
        assert parameters["additionalProperties"] is False
        assert set(parameters["required"]) <= set(parameters["properties"])
