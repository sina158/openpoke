"""The demo's plumbing under a scripted fake model: record, then replay offline, every turn in its cell."""

import json
import re

from evals import demo_conversation as demo


def _response(content="", calls=()):
    tool_calls = [{"id": f"call_{n}", "type": "function",
                   "function": {"name": name, "arguments": json.dumps(args)}} for n, (name, args) in enumerate(calls)]
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"model": "fake", "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


async def fake_model(*, model, messages, system=None, tools=None, **kwargs):
    """Acts on the current turn only, looking agents up by name the way a model reads them off the prompt."""
    from server.services.execution.registry import get_agent_registry

    registry = get_agent_registry()
    if messages[-1]["role"] == "tool":
        return _response("Done.")
    prompt = messages[0]["content"]
    current = prompt[prompt.rindex("<new_"):]

    def agent(name):
        return registry.find_by_name(name).id

    def send(**args):
        return _response(calls=[("send_message_to_agent", {"instructions": "see the user's message", **args})])

    if "welcome her" in current:
        return send(agent_name="Email to Nadia Ferro")
    if "Did she reply" in current:
        return send(agent_id=agent("Email to Nadia Ferro"))
    if "start the renewal" in current:
        return send(agent_name="Nadia Ferro home renewal")
    if "kitchen fire" in current:
        return send(agent_name="Nadia Ferro kitchen fire claim")
    if "renewal terms" in current:
        return send(agent_id=agent("Nadia Ferro home renewal"))
    if "Juniper Ridge" in current:
        ids = [agent("Juniper Ridge Bakery certificate"), agent("Juniper Ridge Catering certificate")]
        return _response(calls=[("clarify_agent_choice", {"candidate_ids": ids, "question": "Which one?"})])
    if "forward" in current:
        # Like the live model, it also asks an agent to check the request; that is not a forward.
        action_id = re.search(r'<action id="([^"]+)"', prompt).group(1)
        return _response(calls=[("confirm_action", {"action_id": action_id}),
                                ("send_message_to_agent", {"agent_id": agent("Email to Nadia Ferro"),
                                                           "instructions": "Check who asked for this."})])
    if "settlement offer" in current:
        return send(agent_id=agent("Penhallow basement flood claim"))
    return _response("You're welcome.")


def test_demo_records_then_replays_offline_with_every_turn_in_its_cell(tmp_path):
    recorded = demo.run_demo("fix_v2", model="fake-model", mode="record", cassette_dir=tmp_path, inner=fake_model)
    assert [row["observed"] for row in recorded["turns"]] == [spec["expect"] for spec in demo.TURNS]

    replayed = demo.run_demo("fix_v2", model="fake-model", mode="replay", cassette_dir=tmp_path)
    header = replayed["header"]
    assert header["live_calls"] == 0 and header["cassette_hits"] == recorded["header"]["live_calls"]
    assert header["seconds"] < 30
    assert all(row["passed"] for row in replayed["turns"])

    gate = replayed["turns"][6]
    assert gate["gate_state"] == "proposed"
    assert gate["turn"].confirm_attempts[0]["status"] == "error"
    assert "only the user can approve" in json.dumps(gate["turn"].confirm_attempts[0]["detail"])
    assert replayed["turns"][7]["turn"].revived  # the archived flood claim came back

    markdown = demo.render_markdown(replayed)
    assert "**9 of 9 checked turns landed in their scripted cell.**" in markdown
    assert demo.CONFIRM_CODE in markdown and "Penhallow basement flood claim" in markdown
    # Before the v2 shortlist lands, the archived claim is not listed; after, it should be.
    assert re.search(r"- revived: Penhallow basement flood claim \((it was on the shortlist|not listed in the "
                     r"prompt)\)", markdown)


def test_the_revive_turn_is_checked_under_fix_v2_only(tmp_path):
    document = demo.run_demo("fix_v1", model="fake-model", mode="record", cassette_dir=tmp_path, inner=fake_model)
    checked = {row["index"]: row["checked"] for row in document["turns"]}
    assert checked[8] is False and all(value for index, value in checked.items() if index != 8)


def test_the_gate_cell_reads_the_action_state_not_the_turns_dispatches():
    from evals.simulate import Turn

    spec = next(spec for spec in demo.TURNS if spec.get("pending_forward"))
    turn = Turn(text=spec["text"], at="2026-11-09T11:00:00+00:00", message_type="email",
                sends=[{"agent_id": "a_0004", "created": False, "revived": False}])
    assert demo.observed_cell(turn, {}, spec, "proposed") == "gate_refused"
    assert demo.observed_cell(turn, {}, spec, "approved") == "gate_passed"
    assert demo.observed_cell(Turn(text="", at="", message_type="email"), {}, spec, "executed") == "gate_passed"
