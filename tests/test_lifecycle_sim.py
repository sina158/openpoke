"""The lifecycle runner under a scripted fake model, and its measures on a tiny month with known answers."""

import json
import os
from datetime import timedelta

import pytest

from evals import run_lifecycle
from evals.lifecycle_scenario import START, build_scenario, day_start


def _at(day, hour):
    return (day_start(day) + timedelta(hours=hour)).isoformat()


def _seed(matter_id, client_key, name, days_ago):
    return {"matter_id": matter_id, "client_key": client_key, "name": name, "days_ago": days_ago,
            "first_request": f"Handle the {name} for {client_key}.", "last_response": "Waiting to hear back.",
            "last_active_at": (START - timedelta(days=days_ago) + timedelta(hours=10)).isoformat()}


def _message(msg_id, day, hour, client_key, kind, matter_ids, text):
    return {"msg_id": msg_id, "day": day, "at": _at(day, hour), "text": text, "client_key": client_key,
            "gold": {"kind": kind, "matter_ids": matter_ids}}


# Three days, five messages: one correct create, one duplicate, one cross-client wrong reuse, one revive of
# an agent archived on day 1, one clarification of an ambiguous message.
TINY = {
    "seed": 1, "days": 3,
    "matters": {"mA": {"client_key": "c-arbor"}, "mB": {"client_key": "c-arbor"},
                "mC": {"client_key": "c-marlow"}, "mD": {"client_key": "c-marlow"}},
    "seed_roster": [_seed("mA", "c-arbor", "Arbor auto claim", 40), _seed("mB", "c-arbor", "Arbor home renewal", 2),
                    _seed("mC", "c-marlow", "Marlow Glass Works certificate", 1)],
    "messages": [
        _message("d01-1", 1, 9, "c-marlow", "new", ["mD"], "Get a CGL quote for Marlow Glass Works."),
        _message("d01-2", 1, 10, "c-marlow", "matter", ["mD"], "Any price yet on the Marlow quote?"),
        _message("d02-1", 2, 9, "c-arbor", "matter", ["mB"], "Did Quentin sign the home renewal forms?"),
        _message("d02-2", 2, 10, "c-arbor", "matter", ["mA"], "The adjuster on Quentin's auto claim called back."),
        _message("d03-1", 3, 9, "c-arbor", "ambiguous", ["mA", "mB"], "Any update for Quentin?"),
    ],
}

SCRIPT = {
    "Get a CGL quote": ("send", {"agent_name": "Marlow Glass Works CGL quote"}),
    "Any price yet": ("send", {"agent_name": "Marlow quote follow-up", "confirm_new": True}),
    "sign the home renewal": ("send", {"agent_name": "Marlow Glass Works certificate"}),
    "auto claim called back": ("send", {"agent_name": "Arbor auto claim"}),
    "Any update for Quentin": ("clarify", ["Arbor auto claim", "Arbor home renewal"]),
}


def _response(content="", calls=()):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"id": f"call_{n}", "type": "function",
                                  "function": {"name": name, "arguments": json.dumps(args)}}
                                 for n, (name, args) in enumerate(calls)]
    return {"model": "fake", "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(messages_text(message)) + 50, "completion_tokens": 5}}


def messages_text(message):
    return json.dumps(message)


async def scripted_model(*, model, messages, system=None, tools=None, **kwargs):
    from server.services.execution.registry import get_agent_registry

    if messages[-1]["role"] == "tool":
        return _response("Done.")
    prompt = messages[0]["content"]
    current = prompt[prompt.rindex("<new_"):]
    for needle, (action, payload) in SCRIPT.items():
        if needle in current:
            if action == "clarify":
                ids = [get_agent_registry().find_by_name(name).id for name in payload]
                return _response(calls=[("clarify_agent_choice", {"candidate_ids": ids, "question": "Which?"})])
            return _response(calls=[("send_message_to_agent", {"instructions": "as asked", **payload})])
    return _response("Noted.")


def test_grading_counts_one_duplicate_one_wrong_reuse_and_one_revive():
    seeded = {"a_0001": "mA", "a_0002": "mB", "a_0003": "mC"}
    records = [
        {"gold": {"kind": "new", "matter_ids": ["mD"]}, "client_key": "c-marlow", "clarified": [],
         "archived_at_start": ["a_0001"], "sends": [{"agent_id": "a_0004", "created": True}]},
        {"gold": {"kind": "matter", "matter_ids": ["mD"]}, "client_key": "c-marlow", "clarified": [],
         "archived_at_start": ["a_0001"], "sends": [{"agent_id": "a_0005", "created": True}]},
        {"gold": {"kind": "matter", "matter_ids": ["mB"]}, "client_key": "c-arbor", "clarified": [],
         "archived_at_start": ["a_0001"], "sends": [{"agent_id": "a_0003", "created": False}]},
        {"gold": {"kind": "matter", "matter_ids": ["mA"]}, "client_key": "c-arbor", "clarified": [],
         "archived_at_start": ["a_0001"], "sends": [{"agent_id": "a_0001", "created": False}]},
        {"gold": {"kind": "ambiguous", "matter_ids": ["mA", "mB"]}, "client_key": "c-arbor",
         "clarified": ["a_0001", "a_0002"], "archived_at_start": [], "sends": []},
    ]
    graded = run_lifecycle.grade_turns(records, seeded, TINY["matters"])
    total = {key: sum(g[key] for g in graded) for key in graded[0] if key != "needed"}
    assert total == {"duplicates": 1, "wrong_reuse": 1, "cross_client": 1, "correct_reuse": 1,
                     "correct_create": 1, "ambiguous_guess": 0, "revived": 1, "revive_due": 1, "clarified": 1,
                     "over_clarified": 0, "no_route": 0}
    assert graded[1]["needed"] == {"mD": ["a_0004"]}  # the duplicate was created while mD had an agent


def test_sending_an_ambiguous_message_to_one_candidate_is_a_guess_not_a_wrong_reuse():
    record = {"gold": {"kind": "ambiguous", "matter_ids": ["mA", "mB"]}, "client_key": "c-arbor", "clarified": [],
              "archived_at_start": [], "sends": [{"agent_id": "a_0002", "created": False}]}
    (graded,) = run_lifecycle.grade_turns([record], {"a_0001": "mA", "a_0002": "mB"}, TINY["matters"])
    assert (graded["ambiguous_guess"], graded["wrong_reuse"], graded["no_route"]) == (1, 0, 0)


def test_slope():
    assert run_lifecycle.slope([(1, 10.0), (2, 12.0), (3, 14.0)]) == pytest.approx(2.0)
    assert run_lifecycle.slope([(1, 10.0)]) is None


def test_the_runner_end_to_end_on_the_tiny_month(tmp_path):
    run = run_lifecycle.simulate("fix_v1", model="fake-model", mode="record", days=3, cassette_dir=tmp_path,
                                 inner=scripted_model, scenario=TINY)
    summary = run_lifecycle.score(run, TINY)
    assert (summary["duplicates"], summary["wrong_reuse"], summary["cross_client"]) == (1, 1, 1)
    assert (summary["revived"], summary["revive_due"], summary["revive_rate"]) == (1, 1, 1.0)
    assert (summary["clarifications"], summary["clarified"], summary["correct_create"]) == (1, 1, 1)
    assert (summary["archive_events"], summary["revive_events"]) == (1, 1)
    assert [row["ideal_live"] for row in summary["series"]] == [3, 4, 4]
    assert [row["active"] for row in summary["series"]] == [4, 5, 5]
    assert summary["reachability_last_day"]["needing_an_agent"] == 1

    replayed = run_lifecycle.simulate("fix_v1", model="fake-model", mode="replay", days=3, cassette_dir=tmp_path,
                                      scenario=TINY)
    assert replayed["header"]["live_calls"] == 0
    assert run_lifecycle.score(replayed, TINY)["series"] == summary["series"]


def test_the_baseline_never_archives(tmp_path):
    run = run_lifecycle.simulate("baseline", model="fake-model", mode="record", days=3, cassette_dir=tmp_path,
                                 inner=scripted_model, scenario=TINY)
    summary = run_lifecycle.score(run, TINY)
    assert summary["archive_events"] == 0 and summary["revive_due"] == 0 and summary["revive_rate"] is None
    assert (summary["duplicates"], summary["wrong_reuse"]) == (1, 1)


def test_outputs_merge_arms_into_one_summary(tmp_path):
    for arm in ("baseline", "fix_v2"):
        run = run_lifecycle.simulate(arm, model="fake-model", mode="record", days=3, cassette_dir=tmp_path / "c",
                                     inner=scripted_model, scenario=TINY)
        path = run_lifecycle.write_outputs(run, run_lifecycle.score(run, TINY), tmp_path)
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1 + len(TINY["messages"])
    document = json.loads((tmp_path / "LIFECYCLE.json").read_text(encoding="utf-8"))
    assert sorted(document["arms"]) == ["baseline", "fix_v2"]
    markdown = (tmp_path / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert "| measure | baseline | fix_v2 |" in markdown and "| 3 |" in markdown


def test_the_real_month_runs_under_a_fake_model(tmp_path):
    async def always_create(*, model, messages, system=None, tools=None, **kwargs):
        if messages[-1]["role"] == "tool":
            return _response("Done.")
        prompt = messages[0]["content"]
        words = prompt[prompt.rindex("<new_"):].split("\n")[1].split()[:5]
        return _response(calls=[("send_message_to_agent", {"agent_name": " ".join(words), "instructions": "go"})])

    scenario = build_scenario()
    run = run_lifecycle.simulate("fix_v2", model="fake-model", mode="record", days=2, cassette_dir=tmp_path,
                                 inner=always_create, scenario=scenario)
    summary = run_lifecycle.score(run, scenario)
    assert summary["turns"] == sum(1 for m in scenario["messages"] if m["day"] <= 2)
    assert summary["errors"] == 0
    assert run["days"][0]["tick_archived"]  # the agents seeded more than 30 days back go on day 1
    assert summary["archive_events"] == 9


def test_the_command_line_replays_a_recorded_run_and_refuses_a_missing_one(tmp_path, capsys):
    run_lifecycle.simulate("fix_v2", model="fake-model", mode="record", days=2, cassette_dir=tmp_path / "c",
                           inner=scripted_model)
    out = tmp_path / "out"
    common = ["--days", "2", "--model", "fake-model", "--replay", "--cassette-dir", str(tmp_path / "c"),
              "--out-dir", str(out)]
    assert run_lifecycle.main(["--arms", "fix_v2", "--transcript", *common]) == 0
    assert "fix_v2: " in capsys.readouterr().out
    transcript = (out / "lifecycle_fix_v2_fake-model_d2.md").read_text(encoding="utf-8")
    assert "## Day 2:" in transcript and "graded:" in transcript and "Archived at 07:00" in transcript
    assert run_lifecycle.main(["--arms", "baseline", *common]) == 2
    assert "stopped, nothing written" in capsys.readouterr().err


def test_a_live_run_without_a_key_is_refused(monkeypatch):
    monkeypatch.delenv("OPENPOKE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        run_lifecycle.main(["--record", "--days", "1"])


def test_reachability_counts_a_message_only_when_each_needed_matter_has_an_agent_in_the_prompt():
    def record(msg_id, day, shown, archived=()):
        return {"msg_id": msg_id, "day": day, "gold": {"kind": "matter", "matter_ids": ["mA"]},
                "shown_ids": list(shown), "archived_at_start": list(archived)}

    records = [record("d03-1", 3, ["a_0002"], archived=["a_0001"]), record("d03-2", 3, ["a_0001", "a_0003"]),
               record("d03-3", 3, ["a_0001"]), record("d03-4", 3, []), record("d02-1", 2, [])]
    graded = [{"needed": {"mA": ["a_0001"]}}, {"needed": {"mA": ["a_0001"], "mB": ["a_0003"]}},
              {"needed": {"mA": ["a_0001"], "mB": ["a_0003"]}}, {"needed": {}}, {"needed": {"mA": ["a_0001"]}}]
    result = run_lifecycle.reachability(records, graded, 3)
    assert (result["needing_an_agent"], result["reachable"]) == (3, 1)
    assert [miss["msg_id"] for miss in result["misses"]] == ["d03-1", "d03-3"]
    assert result["misses"][0]["archived"] == ["a_0001"]


def test_the_summary_states_the_roster_size_and_that_the_shared_client_check_is_inert(tmp_path):
    assert run_lifecycle.shared_client_floor() == 100
    run = run_lifecycle.simulate("fix_v1", model="fake-model", mode="record", days=3, cassette_dir=tmp_path / "c",
                                 inner=scripted_model, scenario=TINY)
    summary = run_lifecycle.score(run, TINY)
    assert (summary["seeded_agents"], summary["roster_end"]) == (3, 5)
    run_lifecycle.write_outputs(run, summary, tmp_path)
    markdown = (tmp_path / "LIFECYCLE.md").read_text(encoding="utf-8")
    assert ("Roster size: 3 agents pre-seeded, at most 5 at the end of the run (live and archived). The "
            "shared-client check is inert below 100 agents, so this run says nothing about it.") in markdown


def test_an_arm_sets_the_v2_switches_whatever_the_shell_says(monkeypatch, tmp_path):
    from evals.cassette import Cassette
    from evals.simulate import Simulation
    from server import config

    monkeypatch.setenv("OPENPOKE_V2_DISABLE", "c1,c3,c4")
    monkeypatch.setenv("OPENPOKE_SHORTLIST_EVIDENCE", "1")
    monkeypatch.setenv("OPENPOKE_EXECUTION_MEMORY", "retrieved")
    with Simulation("fix_v2", "fake-model", Cassette("pin", "replay", tmp_path)).running():
        assert all(config.v2_change_enabled(change) for change in ("c1", "c3", "c4"))
        assert not config.shortlist_evidence() and config.execution_memory() == "recent"
    assert os.environ["OPENPOKE_V2_DISABLE"] == "c1,c3,c4"
