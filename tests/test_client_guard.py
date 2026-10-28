"""The client guard (OPENPOKE_CLIENT_GUARD=1, v2.1): a send may only reach a client the turn's message names."""

import asyncio
import json

import pytest

from server.agents.interaction_agent import tools
from server.agents.interaction_agent.runtime import InteractionAgentRuntime
from server.config import client_guard, get_settings
from server.services.execution import get_agent_registry, get_execution_agent_logs
from server.services.execution.card_store import cards_for

FILLER = 100
# Two clients with one matter each, a second matter of the first (same email), and an agent whose log names
# nobody it could be told apart by.
TARGETS = {
    "nadia": ("Home renewal for Nadia Okafor",
              "Renew the home policy HO-4410-21 for Nadia Okafor, nadia.okafor@example.ca"),
    "nadia_auto": ("Auto quote for Nadia Okafor", "Quote the new car for her, write to nadia.okafor@example.ca"),
    "omar": ("Auto quote for Omar Lindqvist", "Quote the car for Omar Lindqvist, omar.lindqvist@example.ca"),
    "notes": ("Office notes", "keep a list of things to do this week"),
}


class _Loop:
    def __init__(self):
        self.dispatched = 0

    def create_task(self, coro):
        self.dispatched += "_execute_async" in coro.__qualname__
        coro.close()


def _seed(filler):
    registry, logs = get_agent_registry(), get_execution_agent_logs()
    for i in range(filler):
        surname = f"Testcase{chr(65 + i // 26)}{chr(97 + i % 26)}"
        name = f"Renewal for Pat {surname}"
        registry.get_or_create(name)
        logs.record_request(name, f"Renew the policy for Pat {surname}, pat.{surname.lower()}@example.ca")
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
    monkeypatch.setenv("OPENPOKE_CLIENT_GUARD", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    get_settings.cache_clear()
    return fake, _seed(FILLER)


def _refused(ids, text, *keys):
    cards = cards_for(get_agent_registry().list(status=None))
    conflict = tools.unnamed_client_conflict(text, [ids[key] for key in keys], cards)
    return None if conflict is None else [key for key in keys if ids[key] in conflict["agent_ids"]]


def test_a_two_client_turn_whose_message_names_one_refuses_the_other(roster):
    _, ids = roster
    assert _refused(ids, "chase Nadia Okafor about the renewal", "nadia", "omar") == ["omar"]
    cards = cards_for(get_agent_registry().list(status=None))
    conflict = tools.unnamed_client_conflict("chase Nadia Okafor", [ids["nadia"], ids["omar"]], cards)
    assert conflict == {"status": "refused", "reason": "client_not_named", "agent_ids": [ids["omar"]],
                        "message": tools.CLIENT_GUARD_MESSAGE}


def test_a_message_that_names_both_clients_is_a_parallel_send(roster):
    _, ids = roster
    assert _refused(ids, "chase Nadia Okafor and ask omar.lindqvist@example.ca", "nadia", "omar") is None


def test_a_two_client_turn_on_a_message_that_names_nobody_refuses_both(roster):
    _, ids = roster
    assert _refused(ids, "the vet who asked about her renewal, anything back?", "nadia", "omar") == ["nadia", "omar"]


def test_single_client_sends_pass(roster):
    _, ids = roster
    assert _refused(ids, "chase Nadia Okafor", "nadia") is None
    assert _refused(ids, "for her: yes, go ahead", "omar") is None  # names nobody
    # A policy on the home renewal names the client of the auto quote too: the two share her email.
    assert _refused(ids, "is HO-4410-21 sorted? price her car as well", "nadia_auto") is None
    assert _refused(ids, "is HO-4410-21 sorted?", "nadia", "nadia_auto") is None


def test_reusing_another_clients_agent_is_refused(roster):
    _, ids = roster
    assert _refused(ids, "Nadia Okafor wants the car priced", "omar") == ["omar"]


def test_an_agent_with_no_identifying_entity_is_never_judged(roster):
    _, ids = roster
    assert _refused(ids, "Nadia Okafor wants the car priced", "notes") is None
    assert _refused(ids, "anything new?", "notes", "omar") is None


def test_below_a_hundred_agents_nothing_is_identifying_so_the_guard_is_inert(fresh_stores):
    ids = _seed(0)
    assert _refused(ids, "Nadia Okafor wants the car priced", "omar") is None


def _send(agent_id):
    return {"agent_id": agent_id, "instructions": "Please follow up."}


def _results(rounds, text):
    """One assistant message per entry of `rounds` (each a list of sends) on a turn started by `text`; returns
    the tool results in order."""
    runtime = InteractionAgentRuntime()
    runtime.turn_text = text
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
    messages = [{"role": "user", "content": text}]
    asyncio.run(runtime._run_interaction_loop("system", messages))
    assert replies == []
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def _requests(agent_id):
    name = get_agent_registry().get(agent_id).name
    return [text for tag, _, text in get_execution_agent_logs().iter_entries(name) if tag == "agent_request"]


def test_runtime_refuses_only_the_unnamed_clients_send_and_tells_the_model_why(roster):
    loop, ids = roster
    before = _requests(ids["omar"])

    results = _results([[_send(ids["nadia"]), _send(ids["omar"])]], "chase Nadia Okafor about the renewal")

    assert [r["status"] for r in results] == ["success", "error"] and loop.dispatched == 1
    refusal = results[1]["error"]
    assert refusal["reason"] == "client_not_named" and refusal["agent_ids"] == [ids["omar"]]
    assert "clarify_agent_choice" in refusal["message"] and "names" in refusal["message"]
    assert _requests(ids["omar"]) == before


def test_runtime_sends_both_when_the_message_names_both(roster):
    loop, ids = roster
    results = _results([[_send(ids["nadia"]), _send(ids["omar"])]], "ping Nadia Okafor and Omar Lindqvist")
    assert [r["status"] for r in results] == ["success", "success"] and loop.dispatched == 2


def test_runtime_lets_a_new_agent_through_beside_a_named_one(roster):
    loop, ids = roster
    create = {"agent_name": "Home quote for Ines Valdez", "instructions": "Quote it.", "confirm_new": True}
    results = _results([[_send(ids["nadia"]), create]], "chase Nadia Okafor, and quote Ines Valdez a home")
    assert [r["status"] for r in results] == ["success", "success"] and loop.dispatched == 2


def test_a_later_rounds_send_to_another_client_is_refused(roster):
    loop, ids = roster
    first, second = _results([[_send(ids["nadia"])], [_send(ids["omar"])]], "chase Nadia Okafor")
    assert first["status"] == "success" and second["error"]["reason"] == "client_not_named"
    assert loop.dispatched == 1


def test_a_spray_split_across_rounds_on_a_message_naming_nobody_is_refused_in_the_later_round(roster):
    loop, ids = roster
    results = _results([[_send(ids["nadia"])], [_send(ids["nadia"])], [_send(ids["omar"])]], "any news on that?")
    assert [r["status"] for r in results] == ["success", "success", "error"] and loop.dispatched == 2


def test_the_shared_client_check_still_answers_first(roster):
    # Both checks refuse both sends here (one client's two agents, and not the client the message names).
    loop, ids = roster
    results = _results([[_send(ids["nadia"]), _send(ids["nadia_auto"])]], "Omar Lindqvist asked about it")
    assert [r["error"]["reason"] for r in results] == ["shared_client", "shared_client"] and loop.dispatched == 0


@pytest.mark.parametrize("env", [{"OPENPOKE_CLIENT_GUARD": "0"}, {"OPENPOKE_ROUTING_PROFILE": "v1"},
                                 {"OPENPOKE_PROMPT_VARIANT": "original"}])
def test_off_by_flag_under_profile_v1_and_under_the_upstream_prompt(roster, monkeypatch, env):
    loop, ids = roster
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    results = _results([[_send(ids["nadia"])], [_send(ids["omar"])]], "chase Nadia Okafor")
    assert [r["status"] for r in results] == ["success", "success"] and loop.dispatched == 2


def test_the_flag_is_opt_in_and_only_under_v2(monkeypatch):
    monkeypatch.delenv("OPENPOKE_CLIENT_GUARD", raising=False)
    monkeypatch.delenv("OPENPOKE_ROUTING_PROFILE", raising=False)
    assert not client_guard()
    monkeypatch.setenv("OPENPOKE_CLIENT_GUARD", "1")
    assert client_guard()
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v1")
    assert not client_guard()


def test_runtime_reads_the_turn_text_from_the_users_message_and_the_agents_report(roster, monkeypatch):
    _, ids = roster
    seen = []
    monkeypatch.setattr("server.agents.interaction_agent.runtime.check_client_guard",
                        lambda calls, sent, text: seen.append(text) or {})
    send = {"id": "c", "type": "function",
            "function": {"name": "send_message_to_agent", "arguments": json.dumps(_send(ids["nadia"]))}}

    for start, text in (("execute", "chase Nadia Okafor"), ("handle_agent_message", "Omar Lindqvist replied")):
        replies = [{"role": "assistant", "content": "", "tool_calls": [send]}, {"role": "assistant", "content": "Ok."}]

        async def fake_call(system_prompt, messages):
            return {"choices": [{"message": replies.pop(0)}]}

        runtime = InteractionAgentRuntime()
        runtime._make_llm_call = fake_call
        seen.clear()
        asyncio.run(getattr(runtime, start)(text))
        assert seen == [text] and replies == []
