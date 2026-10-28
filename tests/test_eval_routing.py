"""Routing harness: every grading cell from synthetic calls, and an offline run through the real loop."""

import json
import re

import pytest

from evals import run_routing
from evals.generate import load_cases, load_pool
from evals.run_routing import RunConfig, grade, resolve_call

BY_ID = {"g_1": "g_1", "g_2": "g_2", "g_3": "g_3", "a_0001": "g_1"}
BY_SLUG = {"acme-coi": "g_1", "acme-claim": "g_2", "zenith-coi": "g_3"}
CLIENTS = {"g_1": "biz-acme", "g_2": "biz-acme", "g_3": "biz-zenith"}


def _case(kind, ids, client="biz-acme"):
    return {"gold": {"kind": kind, "agent_ids": ids}, "client_key": client}


def _cell(case, *calls):
    resolved = [resolve_call(name, args, BY_ID, BY_SLUG) for name, args in calls]
    return grade(case, resolved, CLIENTS)


def _send(**args):
    return ("send_message_to_agent", {"instructions": "x", **args})


@pytest.mark.parametrize("calls, expected", [
    ([_send(agent_id="a_0001")], ("correct_reuse", False)),
    ([_send(agent_name="Acme COI")], ("correct_reuse", False)),
    ([_send(agent_name="acme   coi")], ("correct_reuse", False)),
    ([_send(agent_name="Acme claim")], ("wrong_reuse", False)),
    ([_send(agent_name="Zenith COI")], ("wrong_reuse", True)),
    ([_send(agent_name="Acme COI 2")], ("duplicate", False)),
    ([("clarify_agent_choice", {"candidate_ids": ["g_1", "g_2"], "question": "which?"})], ("over_clarify", False)),
    ([], ("no_route", False)),
    ([_send(agent_id="a_9999")], ("error", False)),
    ([_send()], ("error", False)),
    ([("send_message_to_agent", {"__invalid_arguments__": "invalid json"})], ("error", False)),
])
def test_reuse_gold_cells(calls, expected):
    assert _cell(_case("reuse", ["g_1"]), *calls) == expected


def test_create_gold_cells():
    case = _case("create", [], client="biz-newco")
    assert _cell(case, _send(agent_name="Newco quote")) == ("correct_create", False)
    assert _cell(case, _send(agent_name="Zenith COI")) == ("hijack", True)
    assert _cell(case, ("clarify_agent_choice", {"candidate_ids": ["g_3"]})) == ("over_clarify", False)


def test_clarify_gold_cells():
    case = _case("clarify", ["g_1", "g_2"])
    assert _cell(case, ("clarify_agent_choice", {"candidate_ids": ["g_1", "g_2"]})) == ("correct_clarify", False)
    assert _cell(case, _send(agent_id="g_1")) == ("wrong_guess", False)
    assert _cell(case, _send(agent_name="Something new")) == ("wrong_guess", False)


def test_parallel_gold_is_set_equality():
    case = _case("parallel", ["g_1", "g_3"], client="biz-acme|biz-zenith")
    assert _cell(case, _send(agent_id="g_3"), _send(agent_name="Acme COI")) == ("correct_reuse", False)
    assert _cell(case, _send(agent_id="g_3")) == ("partial", False)
    assert _cell(case, _send(agent_id="g_3"), _send(agent_name="Acme COI new")) == ("duplicate", False)
    assert _cell(case, _send(agent_id="g_3"), _send(agent_id="g_2")) == ("wrong_reuse", False)


def _tool_call(name, args, n=0):
    return {"id": f"call_{n}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _reply(content="", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": "fake", "usage": {"prompt_tokens": 100, "completion_tokens": 5, "cost": 0,
                                       "cost_details": {"list_cost": 0.001}},
            "choices": [{"message": message, "finish_reason": "stop"}]}


def test_offline_run_through_the_real_loop(tmp_path):
    from server.agents.interaction_agent.agent import build_system_prompt

    pool = load_pool()
    cases = load_cases(split="all")
    explicit = next(c for c in cases if c["category"] == "explicit")
    create = next(c for c in cases if c["category"] == "should_create")
    ambiguous = next(c for c in cases if c["category"] == "ambiguous")
    seen = []

    async def fake_llm(*, model, messages, system=None, tools=None, api_key=None, **kwargs):
        prompt = messages[0]["content"]
        seen.append({"system": system, "prompt": prompt, "tools": [t["function"]["name"] for t in tools],
                     "role": kwargs.get("role"), "turns": len(messages)})
        if explicit["message"] in prompt:
            name = pool[explicit["gold"]["agent_ids"][0]]["name"]
            return _reply(tool_calls=[_tool_call("send_message_to_user", {"message": "On it."}, 0),
                                      _tool_call("send_message_to_agent", {"agent_name": name,
                                                                           "instructions": "go"}, 1)])
        if create["message"] in prompt:
            if len(messages) == 1:
                return _reply(tool_calls=[_tool_call("send_message_to_user", {"message": "Starting."})])
            return _reply(tool_calls=[_tool_call("send_message_to_agent", {"agent_name": "Fresh matter",
                                                                           "instructions": "go"})])
        return _reply(content="Which certificate do you mean?")

    config = RunConfig(sizes=(5, 50), split="all", mode="record", cassette_dir=tmp_path, concurrency=4,
                       case_ids=[explicit["case_id"], create["case_id"], ambiguous["case_id"]])
    document = run_routing.run(config, inner=fake_llm)
    cells = {(r["case_id"], r["size"]): r["cell"] for r in document["records"]}
    assert cells == {
        (explicit["case_id"], 5): "correct_reuse", (explicit["case_id"], 50): "correct_reuse",
        (create["case_id"], 5): "correct_create", (create["case_id"], 50): "correct_create",
        (ambiguous["case_id"], 5): "no_route", (ambiguous["case_id"], 50): "no_route",
    }
    record = next(r for r in document["records"] if r["case_id"] == create["case_id"])
    assert record["llm_calls"] == 2 and record["prompt_tokens"] == 200
    assert record["cost_usd"] == pytest.approx(0.002)
    assert document["header"]["arm"] == "baseline" and document["header"]["n_records"] == 6

    # The real system prompt, tool schemas and rendering reached the model, with the case's roster and history.
    assert all(call["system"] == build_system_prompt() and call["role"] == "interaction" for call in seen)
    assert all("send_message_to_agent" in call["tools"] for call in seen)
    first_turns = [c["prompt"] for c in seen if c["turns"] == 1]
    sizes = sorted(len(re.findall(r"<agent name=", p)) for p in first_turns)
    assert sizes == [5, 5, 5, 50, 50, 50]
    ambiguous_prompt = next(p for p in first_turns if ambiguous["message"] in p)
    assert ambiguous["history"][0]["text"] in ambiguous_prompt
    assert "<new_user_message>" in ambiguous_prompt

    # The same run replays offline from what was just recorded, with no model at all.
    replayed = run_routing.run(RunConfig(sizes=(5, 50), split="all", mode="replay", cassette_dir=tmp_path,
                                         case_ids=config.case_ids))
    assert [r["cell"] for r in replayed["records"]] == [r["cell"] for r in document["records"]]
    assert replayed["header"]["live_calls"] == 0

