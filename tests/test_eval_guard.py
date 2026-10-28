"""The duplicate guard inside the routing harness: a refused create gets one more turn, and is graded on it."""

import json

import pytest

from evals import run_routing
from evals.generate import load_cases, load_pool
from evals.run_routing import RunConfig


def _reply(tool_calls):
    calls = [{"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
             for i, (name, args) in enumerate(tool_calls)]
    return {"model": "fake", "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": calls},
                         "finish_reason": "tool_calls"}]}


def _fake(second_turn):
    """First turn: create the target's matter under a reordered name. Second turn: `second_turn(refusal)`."""
    case = next(c for c in load_cases(split="all") if c["case_id"] == "explicit-002")
    words = load_pool()[case["gold"]["agent_ids"][0]]["name"].split()
    reordered = " ".join(words[1:] + words[:1])

    async def fake(*, model, messages, system=None, tools=None, api_key=None, **kwargs):
        if len(messages) == 1:
            return _reply([("send_message_to_agent", {"agent_name": reordered, "instructions": "go"})])
        refusal = json.loads(messages[-1]["content"])["error"]
        return _reply(second_turn(refusal))

    return case, fake


def _run(arm, fake, case, tmp_path):
    config = RunConfig(arm=arm, sizes=(5,), split="all", mode="live", case_ids=[case["case_id"]],
                       cassette_dir=tmp_path)
    (record,) = run_routing.run(config, inner=fake)["records"]
    return record


@pytest.mark.parametrize("arm", ["prompt_only", "fix"])
def test_guard_answer_leads_to_a_reuse(arm, tmp_path):
    def reuse_suggested(refusal):
        assert refusal["status"] == "possible_duplicates"
        return [("send_message_to_agent", {"agent_id": refusal["possible_duplicates"][0]["agent_id"],
                                           "instructions": "go"})]

    case, fake = _fake(reuse_suggested)
    record = _run(arm, fake, case, tmp_path)
    assert record["guard_fired"] and record["guard_outcome"] == "reused_suggested"
    assert record["routing_rounds"] == 2 and record["llm_calls"] == 2
    assert record["cell"] == "correct_reuse"


def test_confirmed_new_after_the_guard_is_still_a_duplicate(tmp_path):
    def confirm(refusal):
        return [("send_message_to_agent", {"agent_name": "Brand new matter", "confirm_new": True,
                                           "instructions": "go"})]

    case, fake = _fake(confirm)
    record = _run("fix", fake, case, tmp_path)
    assert record["guard_outcome"] == "confirmed_new" and record["cell"] == "duplicate"


def test_baseline_never_runs_the_guard(tmp_path):
    case, fake = _fake(lambda refusal: pytest.fail("the baseline must not get a second routing turn"))
    record = _run("baseline", fake, case, tmp_path)
    assert not record["guard_fired"] and record["guard_outcome"] is None
    assert record["routing_rounds"] == 1 and record["cell"] == "duplicate"
