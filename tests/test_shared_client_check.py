"""The v2 shared-client send check: one turn may not send to two agents that look like one client."""

import asyncio
import json

import pytest

from server.agents.interaction_agent import tools
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.config import get_settings
from server.services.execution import get_agent_registry, get_execution_agent_logs
from server.services.execution.card_store import cards_for

FILLER = 100
CARRIER = "Harbour Mutual"
# Two different businesses whose names start the same, two spouses sharing an inbox, and two unrelated
# clients that share only an insurer (the legitimate parallel send).
TARGETS = {
    "sarnia": ("Maplestone Plumbing COI", "Issue a COI for Maplestone Plumbing in Sarnia, contact Ida Ferreira"),
    "barrie": ("Maplestone Plumbing & Heating COI",
               "Issue a COI for Maplestone Plumbing & Heating in Barrie, contact Ida Beaulieu"),
    "ana": ("Home renewal for Ana Example", "Renew the home policy, write to ana.and.ben@example.com"),
    "ben": ("Auto quote for Ben Example", "Quote the new car, write to ana.and.ben@example.com"),
    "delta": ("COI for Delta Landscaping", f"Issue a COI for Delta Landscaping, carrier {CARRIER}"),
    "uma": ("Endorsement for Uma Brookmore", f"Endorse the policy for Uma Brookmore, carrier {CARRIER}"),
}


class _Loop:
    def __init__(self):
        self.dispatched = 0

    def create_task(self, coro):
        self.dispatched += "_execute_async" in coro.__qualname__
        coro.close()


def _surnames():
    return [a + b for a in ("Bel", "Cor", "Dun", "Fal", "Gar", "Hol", "Jen", "Kel", "Lor", "Mor")
            for b in ("ton", "ley", "ford", "wick", "more", "dale", "ham", "by", "son", "well")]


def _seed(filler):
    """Filler clients (every seventh insured by CARRIER) plus TARGETS; returns {key: agent_id}."""
    registry, logs = get_agent_registry(), get_execution_agent_logs()
    for i, surname in enumerate(_surnames()[:filler]):
        name = f"Renewal for Pat {surname}"
        registry.get_or_create(name)
        carrier = f", carrier {CARRIER}" if i % 7 == 0 else ""
        logs.record_request(name, f"Renew the policy for Pat {surname}, pat.{surname.lower()}@example.ca{carrier}")
    ids = {}
    for key, (name, request) in TARGETS.items():
        ids[key] = registry.get_or_create(name)[0].id
        logs.record_request(name, request)
    return ids


@pytest.fixture
def roster(fresh_stores, monkeypatch):
    fake = _Loop()
    monkeypatch.setattr(tools.asyncio, "get_running_loop", lambda: fake)
    monkeypatch.setenv("OPENPOKE_PROMPT_VARIANT", "shortlist")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    return fake, _seed(FILLER)


def _conflict(ids, *keys):
    cards = cards_for(get_agent_registry().list(status=None))
    return tools.shared_client_conflict([ids[key] for key in keys], cards)


def test_two_businesses_sharing_a_name_are_refused(roster):
    _, ids = roster
    assert _conflict(ids, "sarnia", "barrie") == {
        "status": "refused",
        "reason": "shared_client",
        "agent_ids": [ids["sarnia"], ids["barrie"]],
        "message": tools.SHARED_CLIENT_MESSAGE,
    }


def test_spouses_sharing_an_email_are_refused(roster):
    _, ids = roster
    assert _conflict(ids, "ana", "ben")["agent_ids"] == [ids["ana"], ids["ben"]]


def test_a_shared_insurer_is_not_a_client(roster):
    _, ids = roster
    assert _conflict(ids, "delta", "uma") is None


def test_removing_the_document_frequency_filter_refuses_the_parallel_pair(roster, monkeypatch):
    # Negative control: the parallel pair passes only because the insurer is on more than 2% of cards.
    _, ids = roster
    monkeypatch.setattr(tools, "SHARED_CLIENT_MAX_DF", 1.0)
    assert _conflict(ids, "delta", "uma")["agent_ids"] == [ids["delta"], ids["uma"]]


def test_one_target_or_the_same_target_twice_is_never_a_conflict(roster):
    _, ids = roster
    assert _conflict(ids, "sarnia") is None
    assert _conflict(ids, "sarnia", "sarnia") is None


def test_only_the_targets_that_share_something_are_named(roster):
    _, ids = roster
    assert _conflict(ids, "sarnia", "delta", "barrie")["agent_ids"] == [ids["sarnia"], ids["barrie"]]


def test_below_a_hundred_agents_two_cards_are_over_two_percent(fresh_stores):
    # An entity on 2 of fewer than 100 cards is above the cut-off, so the check does not fire there.
    ids = _seed(0)
    assert _conflict(ids, "sarnia", "barrie") is None


def _send(agent_id):
    return {"agent_id": agent_id, "instructions": "Please resend the certificate."}


def _run_turn(sends):
    """One assistant message with `sends`, then a final message; returns the tool messages of the first."""
    runtime = InteractionAgentRuntime()
    replies = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": "send_message_to_agent", "arguments": json.dumps(args)}}
            for i, args in enumerate(sends)
        ]},
        {"role": "assistant", "content": "Which Maplestone do you mean?"},
    ]

    async def fake_call(system_prompt, messages):
        return {"choices": [{"message": replies.pop(0)}]}

    runtime._make_llm_call = fake_call
    messages = [{"role": "user", "content": "resend the Maplestone certificate"}]
    summary = asyncio.run(runtime._run_interaction_loop("system", messages))
    assert replies == [] and summary.last_assistant_text == "Which Maplestone do you mean?"
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"], summary


def _requests(agent_id):
    name = get_agent_registry().get(agent_id).name
    return [text for tag, _, text in get_execution_agent_logs().iter_entries(name) if tag == "agent_request"]


def test_runtime_sends_nothing_to_either_business_and_gives_the_model_another_turn(roster):
    loop, ids = roster
    before = {key: _requests(ids[key]) for key in ("sarnia", "barrie", "delta")}

    results, summary = _run_turn([_send(ids["sarnia"]), _send(ids["barrie"]), _send(ids["delta"])])

    refused = [r for r in results if r["status"] == "error"]
    assert [r["arguments"]["agent_id"] for r in refused] == [ids["sarnia"], ids["barrie"]]
    for result in refused:
        assert result["error"]["reason"] == "shared_client"
        assert result["error"]["agent_ids"] == [ids["sarnia"], ids["barrie"]]
        assert "agent_id" in result["error"]["message"] and "clarify_agent_choice" in result["error"]["message"]
    assert loop.dispatched == 1 and summary.execution_agents == {ids["delta"]}
    assert _requests(ids["sarnia"]) == before["sarnia"] and _requests(ids["barrie"]) == before["barrie"]
    assert _requests(ids["delta"]) == before["delta"] + ["Please resend the certificate."]


def test_runtime_lets_the_parallel_pair_through(roster):
    loop, ids = roster
    results, _ = _run_turn([_send(ids["delta"]), _send(ids["uma"])])
    assert [r["status"] for r in results] == ["success", "success"] and loop.dispatched == 2


@pytest.mark.parametrize("env", [{"OPENPOKE_ROUTING_PROFILE": "v1"}, {"OPENPOKE_V2_DISABLE": "c1"},
                                 {"OPENPOKE_PROMPT_VARIANT": "original"}])
def test_profile_v1_the_disable_flag_and_the_upstream_prompt_send_both(roster, monkeypatch, env):
    loop, ids = roster
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    results, summary = _run_turn([_send(ids["sarnia"]), _send(ids["barrie"])])

    assert [r["status"] for r in results] == ["success", "success"] and loop.dispatched == 2
    assert summary.execution_agents == {ids["sarnia"], ids["barrie"]}


def _run_rounds(rounds):
    """One assistant message per entry of `rounds` (each a list of sends), then a final message."""
    runtime = InteractionAgentRuntime()
    replies = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"call_{r}_{i}", "type": "function",
             "function": {"name": "send_message_to_agent", "arguments": json.dumps(args)}}
            for i, args in enumerate(sends)
        ]}
        for r, sends in enumerate(rounds)
    ] + [{"role": "assistant", "content": "Done."}]

    async def fake_call(system_prompt, messages):
        return {"choices": [{"message": replies.pop(0)}]}

    runtime._make_llm_call = fake_call
    messages = [{"role": "user", "content": "resend the Maplestone certificate"}]
    asyncio.run(runtime._run_interaction_loop("system", messages))
    assert replies == []
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def test_a_spray_split_across_rounds_is_refused_in_the_later_round(roster):
    loop, ids = roster
    before = _requests(ids["barrie"])

    first, second = _run_rounds([[_send(ids["sarnia"])], [_send(ids["barrie"])]])

    assert first["status"] == "success" and second["status"] == "error"
    assert second["error"]["reason"] == "shared_client"
    assert second["error"]["agent_ids"] == [ids["sarnia"], ids["barrie"]]
    assert loop.dispatched == 1 and _requests(ids["barrie"]) == before


def test_a_follow_up_to_the_same_agent_or_an_unrelated_one_passes(roster):
    loop, ids = roster
    results = _run_rounds([[_send(ids["sarnia"])], [_send(ids["sarnia"])], [_send(ids["delta"])]])
    assert [r["status"] for r in results] == ["success"] * 3 and loop.dispatched == 3


def test_a_refused_send_does_not_count_as_sent(roster):
    loop, ids = roster
    results = _run_rounds([[_send(ids["sarnia"]), _send(ids["barrie"])], [_send(ids["barrie"])]])
    assert [r["status"] for r in results] == ["error", "error", "success"] and loop.dispatched == 1


@pytest.mark.parametrize("env", [{"OPENPOKE_ROUTING_PROFILE": "v1"}, {"OPENPOKE_V2_DISABLE": "c1"}])
def test_profile_v1_and_the_disable_flag_send_in_both_rounds(roster, monkeypatch, env):
    loop, ids = roster
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    results = _run_rounds([[_send(ids["sarnia"])], [_send(ids["barrie"])]])
    assert [r["status"] for r in results] == ["success", "success"] and loop.dispatched == 2
