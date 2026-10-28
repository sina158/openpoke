"""The arm flags really switch the rendering, the tool schemas and the prompt (plan 4.3 wiring test)."""

import hashlib
import json
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
    "full_cards": ("full_cards", "shortlist"),
}
NAMES = [f"Renewal for Client {n:02d}" for n in range(12)]
# sha256 of the compact sorted-key JSON of TOOL_SCHEMAS in server/agents/interaction_agent/tools.py at
# upstream commit 5b5f635: send_message_to_agent, send_message_to_user, send_draft, wait.
UPSTREAM_TOOL_SCHEMAS_SHA256 = "c1b5adec8667ff109035b0d15994ce496bd5f1f3026e2e50459cd495cc1402d4"


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
    blob = json.dumps(schemas, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == UPSTREAM_TOOL_SCHEMAS_SHA256
    send = _send_schema(schemas)
    assert set(send["properties"]) == {"agent_name", "instructions"}
    assert send["required"] == ["agent_name", "instructions"]
    assert "Don't worry if the agent name is unrelated" in prompt


def test_prompt_only_shows_the_full_roster_with_ids_but_no_cards(seeded):
    content, schemas, prompt = seeded("prompt_only")

    roster = [(r.id, r.name) for r in get_agent_registry().list()]
    expected = "\n".join(f'<agent id="{agent_id}" name="{name}" />' for agent_id, name in roster)
    assert len(roster) == 12
    assert f"<active_agents>\n{expected}\n</active_agents>" in content
    assert "purpose=" not in content and "mode=" not in content
    names = [s["function"]["name"] for s in schemas]
    assert "clarify_agent_choice" in names and "confirm_action" in names and "search_agents" not in names
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


def test_hybrid_selector_uses_vectors_and_falls_back_to_bm25(seeded, monkeypatch):
    from server.agents.interaction_agent import agent as agent_module

    bm25_content, _, _ = seeded("fix")
    monkeypatch.setenv("OPENPOKE_SELECTOR", "hybrid")

    def unavailable(texts, cache_dir=None):
        raise RuntimeError("fastembed is not installed")

    monkeypatch.setattr(agent_module.embeddings, "embed", unavailable)
    fallback, _, _ = seeded("fix")
    assert fallback == bm25_content

    # Vectors that point the query at client 11 only: fusing them must bring it into the shortlist.
    def fake_embed(texts, cache_dir=None):
        return [[1.0, 0.0]] + [[1.0, 0.0] if "Client 11" in text else [0.0, 1.0] for text in texts[1:]]

    monkeypatch.setattr(agent_module.embeddings, "embed", fake_embed)
    hybrid, _, _ = seeded("fix")
    assert "Renewal for Client 11" not in bm25_content
    assert 'name="Renewal for Client 11"' in hybrid


def _archive_client_03():
    from datetime import datetime, timezone

    registry = get_agent_registry()
    target = registry.find_by_name("Renewal for Client 03")  # the best match for the message
    registry.touch(target.id, now=datetime(2026, 8, 1, tzinfo=timezone.utc))
    now = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
    assert registry.archive_stale(now, idle_days=30, protected_ids=set()) == [target.id]
    return target


def test_under_v1_an_archived_agent_never_appears_in_the_shortlist(seeded, monkeypatch):
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v1")
    target = _archive_client_03()

    content, _, _ = seeded("fix")

    assert target.id not in content and "Renewal for Client 03" not in content
    assert '<active_agents mode="shortlist" shown="8" total="11">' in content
    assert "3 more agents are not shown" in content


@pytest.mark.parametrize("disabled", ["", "c1,c4"])
def test_under_v2_an_archived_agent_is_a_candidate_shown_with_its_status(seeded, monkeypatch, disabled):
    monkeypatch.setenv("OPENPOKE_V2_DISABLE", disabled)
    target = _archive_client_03()

    content, _, _ = seeded("fix")

    assert f'<agent id="{target.id}" name="Renewal for Client 03" status="archived" last_active="87d ago"' in content
    assert '<active_agents mode="shortlist" shown="8" total="12">' in content
    assert "4 more agents are not shown" in content


def test_disabling_c3_keeps_archived_agents_off_the_shortlist(seeded, monkeypatch):
    monkeypatch.setenv("OPENPOKE_V2_DISABLE", "c3")
    target = _archive_client_03()

    content, _, _ = seeded("fix")

    assert target.id not in content and 'total="11"' in content


def test_full_cards_shows_every_card_without_selection_or_search(seeded):
    content, schemas, prompt = seeded("full_cards")

    assert '<active_agents mode="full_cards" total="12">' in content
    shown = re.findall(r'<agent id="(a_\d+)" name="[^"]+" status="active" last_active="0h ago" purpose=', content)
    assert shown == [record.id for record in get_agent_registry().list()]
    assert "pinned=" not in content and "not shown" not in content
    names = [s["function"]["name"] for s in schemas]
    assert "clarify_agent_choice" in names and "search_agents" not in names
    assert prompt == build_system_prompt("shortlist")


def test_the_agent_whose_result_is_handled_is_pinned_as_current(seeded):
    seeded("fix")
    report = "[SUCCESS] Renewal for Client 11: the renewal forms were signed and sent back."

    content = prepare_message_with_history(report, "", message_type="agent")[0]["content"]

    current = re.findall(r'<agent id="(a_\d+)" name="([^"]+)"[^>]* pinned="current"', content)
    assert current == [(get_agent_registry().find_by_name("Renewal for Client 11").id, "Renewal for Client 11")]
