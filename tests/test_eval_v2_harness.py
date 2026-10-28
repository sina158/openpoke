"""Round-two harness: flags per arm, the set key, the shared-client guard round, the evidence check, sampling."""

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from evals import compare, run_routing
from evals.generate import GOLDEN_V2_PATH, POOL_V2_PATH, SEED_V2, load_cases, load_pool
from evals.run_routing import RunConfig

V2_FLAGS = ("OPENPOKE_ROUTING_PROFILE", "OPENPOKE_V2_DISABLE", "OPENPOKE_SHORTLIST_EVIDENCE")


def _reply(tool_calls=()):
    calls = [{"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
             for i, (name, args) in enumerate(tool_calls)]
    message = {"role": "assistant", "content": ""}
    if calls:
        message["tool_calls"] = calls
    return {"model": "fake", "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            "choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]}


def _v2_case(case_id):
    return next(c for c in load_cases(GOLDEN_V2_PATH) if c["case_id"] == case_id)


def _run(arm, fake, case, tmp_path, sizes=(5,), **config):
    config = RunConfig(arm=arm, model="fake", sizes=sizes, case_set="v2", mode="record", cassette_dir=tmp_path,
                       case_ids=[case["case_id"]], **config)
    return run_routing.run(config, inner=fake)


async def _no_route(*, model, messages, system=None, tools=None, api_key=None, **kwargs):
    return _reply()


# ---- arms and sets -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("arm, expected", [
    ("fix_v1", ("v1", "", "0")), ("fix_v2", ("v2", "", "0")), ("fix_v2_no_c3", ("v2", "c3", "0")),
    ("fix_v2_evidence", ("v2", "", "1")),
])
def test_arm_flags_apply_during_the_run_and_are_restored(arm, expected, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "from-the-shell")
    monkeypatch.delenv("OPENPOKE_V2_DISABLE", raising=False)
    seen = []

    async def fake(**kwargs):
        seen.append(tuple(os.environ.get(name) for name in V2_FLAGS))
        return _reply()

    document = _run(arm, fake, _v2_case("v2-explicit-001"), tmp_path)
    assert seen and set(seen) == {expected}
    assert os.environ["OPENPOKE_ROUTING_PROFILE"] == "from-the-shell" and "OPENPOKE_V2_DISABLE" not in os.environ
    header = document["header"]
    assert (header["openpoke_routing_profile"], header["openpoke_v2_disable"],
            header["openpoke_shortlist_evidence"]) == expected
    assert header["openpoke_execution_memory"] == "recent"


def test_round_one_arms_are_pinned_to_v1_and_every_header_says_what_ran(tmp_path, monkeypatch):
    assert all(run_routing.ARMS[arm]["OPENPOKE_ROUTING_PROFILE"] == "v1"
               for arm in ("fix", "prompt_only", "cards_full"))
    monkeypatch.delenv("OPENPOKE_ROUTING_PROFILE", raising=False)
    monkeypatch.setenv("OPENPOKE_EXECUTION_MEMORY", "retrieved")
    header = _run("baseline", _no_route, _v2_case("v2-explicit-001"), tmp_path)["header"]
    assert header["openpoke_routing_profile"] == "v2" and header["openpoke_execution_memory"] == "retrieved"
    assert header["openpoke_v2_disable"] == "" and header["openpoke_shortlist_evidence"] == "0"


def test_an_absent_set_file_is_a_clear_error():
    with pytest.raises(FileNotFoundError, match="routing_v2_paraphrase.jsonl"):
        run_routing.run(RunConfig(case_set="v2_paraphrase"))


def test_the_v2_set_uses_its_own_files_seed_and_names(tmp_path):
    case = _v2_case("v2-near_duplicate-003")
    document = _run("fix_v2", _no_route, case, tmp_path)
    header, (record,) = document["header"], document["records"]
    assert header["set"] == "v2" and header["roster_seed"] == SEED_V2 and header["n_cases"] == 1
    assert (tmp_path / "routing_v2_fix_v2_fake_k8.jsonl").exists()
    pool = load_pool(POOL_V2_PATH)
    assert record["target_idle"] == any(pool[i]["last_active_days_ago"] > 30 for i in case["gold"]["agent_ids"])
    path = run_routing.write_results(document, tmp_path / "out")
    assert "_v2_fix_v2_fake_k8_report_n5_t1_" in path.name
    headline = dict(document, header=dict(header, sizes=[500], trials=3))
    assert run_routing.write_results(headline, tmp_path / "out") != path  # no overwrite across run shapes


def test_new_recordings_never_append_to_round_one_cassettes(tmp_path):
    case = next(c for c in load_cases() if c["category"] == "explicit")
    config = RunConfig(arm="fix", model="fake", sizes=(5,), split="all", mode="record", cassette_dir=tmp_path,
                       case_ids=[case["case_id"]])
    run_routing.run(config, inner=_no_route)
    assert [p.name for p in tmp_path.glob("*.jsonl")] == ["routing_v1_fix_fake_k8.jsonl"]


def test_case_sample_is_seeded_and_stratified():
    cases = load_cases(GOLDEN_V2_PATH)
    sample = run_routing.sample_cases(cases, 30, SEED_V2)
    assert len(sample) == 30 and sample == run_routing.sample_cases(cases, 30, SEED_V2)
    assert sample != run_routing.sample_cases(cases, 30, SEED_V2 + 1)
    counts, totals = Counter(c["category"] for c in sample), Counter(c["category"] for c in cases)
    assert all(abs(counts[c] - 30 * n / len(cases)) < 1 for c, n in totals.items())
    assert sample == [c for c in cases if c in sample]


def test_cards_full_runs_above_fifty_only_on_a_sample(tmp_path):
    with pytest.raises(ValueError):
        run_routing.run(RunConfig(arm="cards_full", sizes=(500,), case_set="v2"))
    config = RunConfig(arm="cards_full", model="fake", sizes=(500,), case_set="v2", mode="record",
                       cassette_dir=tmp_path, cases_sample=1)
    document = run_routing.run(config, inner=_no_route)
    assert document["header"]["cases_sample"] == 1 and document["header"]["sample_seed"] == SEED_V2
    assert "_s1_" in run_routing.write_results(document, tmp_path / "out").name


# ---- shared-client refusals, counted on their own --------------------------------------------------------

def _spray_then(second):
    """Round one sends to both of one client's renewals by name; round two is `second(names)`."""
    case = _v2_case("v2-near_duplicate-002")
    pool = load_pool(POOL_V2_PATH)
    names = [pool[i]["name"] for i in case["target_ids"] + case["hard_negative_ids"]]

    async def fake(*, model, messages, system=None, tools=None, api_key=None, **kwargs):
        return _reply(_send(*names) if len(messages) == 1 else second(names))

    return case, fake


def _send(*names):
    return [("send_message_to_agent", {"agent_name": name, "instructions": "go"}) for name in names]


def test_a_turn_refused_twice_is_counted_as_refused(tmp_path):
    case, fake = _spray_then(lambda names: _send(*names))
    (record,) = _run("fix_v2", fake, case, tmp_path, sizes=(500,))["records"]
    assert record["guard_fired"] == "shared_client" and record["cell"] == "no_route"
    assert record["shared_client_refused"] == 4 and record["ended_refused"]


@pytest.mark.parametrize("arm", ["fix_v2_no_c1", "fix_v1"])
def test_without_the_check_the_spray_is_sent_and_nothing_is_refused(arm, tmp_path):
    case, fake = _spray_then(lambda names: pytest.fail("no second round without the check"))
    (record,) = _run(arm, fake, case, tmp_path, sizes=(500,))["records"]
    assert record["cell"] == "wrong_reuse" and record["guard_fired"] is False
    assert record["shared_client_refused"] == 0 and not record["ended_refused"] and record["same_client_multi_send"]
    assert not record["multi_client_send"]  # one client's two agents


def test_a_send_to_two_clients_is_a_multi_client_send(tmp_path):
    case = _v2_case("v2-near_duplicate-001")  # same stem, two businesses
    pool = load_pool(POOL_V2_PATH)

    async def fake(**kwargs):
        return _reply(_send(*(pool[i]["name"] for i in case["target_ids"] + case["hard_negative_ids"])))

    (record,) = _run("fix_v2", fake, case, tmp_path)["records"]  # N=5: the check is inert below 100 agents
    assert record["multi_client_send"] and not record["same_client_multi_send"] and record["cell"] == "wrong_reuse"


# ---- the evidence check (BARS-v2 A2) -------------------------------------------------------------------------

def test_evidence_check_reads_the_servers_own_card_lines():
    from server.services.execution.cards import AgentCard, render_card

    def card(agent_id, name):
        return AgentCard(agent_id, name, "active", "2026-10-20T09:00:00", "Open a home claim.", ())

    prompt = "\n".join([
        render_card(card("a1", "Orla Fenwick home claim - theft"),
                    evidence=(1, "Open a home claim, loss of June 18 & police report")),
        render_card(card("a2", "Orla Fenwick home claim - hail damage")),
    ])
    assert 'evidence="a1#1: ' in prompt  # the real cite, escaped as the server writes it

    def check(category, terms, picked):
        case = {"category": category, "discriminators": terms}
        rendered = run_routing.Rendered(case, 5, Path("."), "system", [{"role": "user", "content": prompt}], [],
                                        {}, {})
        return run_routing.evidence_check(rendered, picked)

    assert check("near_duplicate", ["June 18"], ["a1"]) is True
    assert check("should_create", ["May 27"], ["a1"]) is False
    assert check("near_duplicate", ["a1#1"], ["a1"]) is False  # the cite id is not evidence
    assert check("near_duplicate", ["June 18"], ["a2"]) is None  # no evidence shown for the pick
    assert check("explicit", ["June 18"], ["a1"]) is None
    assert check("near_duplicate", ["June 18"], []) is None


# ---- compare --------------------------------------------------------------------------------------------------

def _record(case, trial, cell, **extra):
    return {"case_id": case["case_id"], "category": case["category"], "template_id": case["template_id"],
            "split": "report", "trial": trial, "size": 500, "cell": cell, "gold": case["gold"],
            "correct": cell in run_routing.CORRECT_CELLS, "cross_client": False, "resolved": [],
            "user_messages": [], "prompt_tokens": 1000, "latency_ms": 100.0, "cost_usd": 0.001, **extra}


def _document(arm, records, path, **header):
    header = {"arm": arm, "model": "m", "k": 8, "date": "2026-10-28T00:00:00Z", "commit": "abc1234",
              "split": "report", "sizes": [500], "trials": 1, **header}
    path.write_text(json.dumps({"header": header, "records": records}), encoding="utf-8")
    return path


def test_b2a_counts_only_cases_whose_gold_does_not_call_for_two_clients():
    parallel, single = _v2_case("v2-parallel-001"), _v2_case("v2-near_duplicate-001")
    rows = [_record(parallel, 0, "correct_reuse", multi_client_send=True),
            _record(parallel, 1, "wrong_reuse", multi_client_send=True),
            _record(single, 0, "correct_reuse"), _record(single, 1, "correct_reuse")]
    row = compare.summarise(rows, {})
    multi = row["multi_client_send"]
    assert (multi["k"], multi["n"]) == (0, 2) and multi["upper95"] == pytest.approx(1.5)
    assert multi["parallel_gold"] == {"k": 2, "n": 2, "matched_gold": 1}
    key = compare.group_key("fix_v2", run_routing.DEFAULT_MODEL, 8, 500)
    assert compare._b2a({key: row})["b2a_pass"] is True  # correct parallel answers are not violations

    rows.append(_record(single, 2, "wrong_reuse", multi_client_send=True))
    row = compare.summarise(rows, {})
    assert row["multi_client_send"]["k"] == 1 and compare._b2a({key: row})["b2a_pass"] is False
    assert compare._b2a({})["b2a_pass"] is None


def test_a2_is_reported_as_registered_and_corrected():
    near, create = _v2_case("v2-near_duplicate-001"), _v2_case("v2-should_create-001")
    rows = [_record(near, 0, "correct_reuse", evidence_names_discriminator=True),
            _record(near, 1, "wrong_reuse", evidence_names_discriminator=True),
            _record(create, 0, "hijack", evidence_names_discriminator=False),
            _record(create, 1, "correct_create")]
    evidence = compare.summarise(rows, {})["evidence"]
    assert (evidence["registered"]["k"], evidence["registered"]["n"]) == (2, 4)
    assert (evidence["corrected"]["k"], evidence["corrected"]["n"]) == (1, 1)


def test_each_arm_is_paired_against_its_references():
    assert compare.references("baseline") == [] and compare.references("fix_v1") == ["baseline"]
    assert compare.references("fix_v2") == ["baseline", "fix_v1"]
    assert compare.references("fix_v2_no_c3") == ["baseline", "fix_v1", "fix_v2"]
    assert compare.references("fix_v2_evidence") == ["baseline", "fix_v1", "fix_v2"]


def test_compare_never_mixes_sets_and_drops_the_fix_alias_from_v2(tmp_path):
    case = _v2_case("v2-explicit-001")
    v1 = _document("fix_v2", [_record(case, 0, "correct_reuse")], tmp_path / "a.json")
    v2 = _document("fix_v2", [_record(case, 0, "correct_reuse")], tmp_path / "b.json", set="v2")
    alias = _document("fix", [_record(case, 0, "correct_reuse")], tmp_path / "c.json", set="v2")
    headers = [json.loads(p.read_text(encoding="utf-8"))["header"] for p in (v1, v2)]
    assert compare.config_key(headers[0]) != compare.config_key(headers[1])
    assert compare.main([str(v1), str(v2), "--out-dir", str(tmp_path)]) == 1
    assert compare.main([str(v2), str(alias), "--out-dir", str(tmp_path), "--set", "v2"]) == 0
    assert (tmp_path / "RESULTS_v2.md").exists() and not (tmp_path / "RESULTS.md").exists()
    report = json.loads((tmp_path / "RESULTS_v2.json").read_text(encoding="utf-8"))
    assert [row["arm"] for row in report["report"]["groups"]] == ["fix_v2"]
    assert report["skipped_fix_alias_runs"] == ["c.json"]


def test_v2_report_pairs_against_fix_v1_and_splits_by_target_status(tmp_path):
    pool = load_pool(POOL_V2_PATH)
    targeted = [c for c in load_cases(GOLDEN_V2_PATH) if c["gold"]["agent_ids"]]
    idle = [c for c in targeted if any(pool[i]["last_active_days_ago"] > 30 for i in c["gold"]["agent_ids"])][:2]
    active = [c for c in targeted if c not in idle and c["category"] == "near_duplicate"
              and all(pool[i]["last_active_days_ago"] <= 30 for i in c["gold"]["agent_ids"])][:2]
    cases = idle + active
    fix_v2 = [_record(c, 0, "correct_reuse") for c in cases[:3]] + [
        _record(cases[3], 0, "no_route", shared_client_refused=4, ended_refused=True)]
    fix_v2[2]["evidence_names_discriminator"] = True
    fix_v1 = [_record(c, 0, "correct_reuse") for c in cases[:1]] + [_record(c, 0, "wrong_reuse") for c in cases[1:]]
    baseline = [_record(c, 0, "wrong_reuse") for c in cases]
    sampled = [_record(c, 0, "wrong_reuse", size=5000) for c in cases]
    paths = [_document("fix_v2", fix_v2, tmp_path / "a.json", set="v2"),
             _document("fix_v1", fix_v1, tmp_path / "b.json", set="v2"),
             _document("baseline", baseline, tmp_path / "c.json", set="v2"),
             _document("baseline", sampled, tmp_path / "d.json", set="v2", sizes=[5000], cases_sample=30),
             _document("fix_v2", [dict(r, size=5000) for r in fix_v2], tmp_path / "e.json", set="v2",
                       sizes=[5000], cases_sample=30)]
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path), "--set", "v2"]) == 0
    report = json.loads((tmp_path / "RESULTS_v2.json").read_text(encoding="utf-8"))
    assert report["set_mix"] == {"set": "v2", "split": "report", "non_create": 94, "idle_target": 58,
                                 "share": pytest.approx(58 / 94)}
    head = report["report"]
    row = head["by_key"]["fix_v2_m_k8_n500"]
    assert row["target_status"]["idle"]["n"] == 2 and row["target_status"]["active"]["n"] == 2
    assert row["target_status"]["idle"]["k"] == 2 and row["target_status"]["active"]["k"] == 1
    assert row["refused"] == {"turns": 1, "n": 4, "sends": 4}
    assert row["hijack"]["upper95"] == 1.0  # no create cases here: the bound is the whole range
    pairs = head["paired_by_key"]
    assert pairs["fix_v2_vs_fix_v1_m_k8_n500"]["c"] == 2 and pairs["fix_v2_vs_fix_v1_m_k8_n500"]["b"] == 0
    assert pairs["fix_v2_vs_baseline_m_k8_n500"]["c"] == 3
    assert "fix_v1_vs_fix_v1_m_k8_n500" not in pairs and "fix_v1_vs_baseline_m_k8_n500" in pairs
    assert "fix_v2_vs_baseline_m_k8_n5000_s30" in pairs and "baseline_m_k8_n5000_s30" in head["by_key"]
    markdown = (tmp_path / "RESULTS_v2.md").read_text(encoding="utf-8")
    assert "58 of 94 non-create report cases" in markdown and "30 cases, demonstration" in markdown


def test_v2_report_grades_the_bars_it_has_runs_for(tmp_path):
    cases = [c for c in load_cases(GOLDEN_V2_PATH) if c["category"] == "explicit"][:6]

    def document(arm, cell):
        return _document(arm, [_record(c, 0, cell) for c in cases], tmp_path / f"{arm}.json", set="v2",
                         model=run_routing.DEFAULT_MODEL)

    paths = [document("baseline", "wrong_reuse"), document("fix_v1", "wrong_reuse"),
             document("fix_v2", "correct_reuse")]
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path), "--set", "v2"]) == 0
    report = json.loads((tmp_path / "RESULTS_v2.json").read_text(encoding="utf-8"))
    verdict = {v["bar"]: v["verdict"] for v in report["verdicts"]}
    assert verdict["B1"] == "pass"  # 6 of 6 discordant pairs favour fix_v2: McNemar p = 0.031
    assert verdict["B2a"] == "pass" and verdict["B7"] == "pass"
    assert verdict["B2b"] == "pass"  # 0 against 0 counts as below fix_v1 (lead's ruling)
    assert verdict["B6"] == "pending" and verdict["A1"] == "pending" and verdict["B8"] == "pending"
    assert verdict["B9"] == "pending tier 2"
    assert all(verdict[f"Ablation: no {c}"] == "pending tier 2" for c in ("c1", "c3", "c4"))
    assert "## Bars (BARS-v2)" in (tmp_path / "RESULTS_v2.md").read_text(encoding="utf-8")


def test_a_replay_miss_aborts_unless_misses_are_graded_as_errors(tmp_path):
    from evals.cassette import CassetteMiss

    ids = [c["case_id"] for c in load_cases(GOLDEN_V2_PATH)[:11]]
    recorded = RunConfig(arm="fix_v2", model="fake", sizes=(5,), case_set="v2", mode="record", cassette_dir=tmp_path,
                         case_ids=ids[:10])
    run_routing.run(recorded, inner=_no_route)  # the eleventh case is never recorded, as if it failed live
    config = RunConfig(arm="fix_v2", model="fake", sizes=(5,), case_set="v2", mode="replay", cassette_dir=tmp_path,
                       case_ids=ids)
    with pytest.raises(CassetteMiss):
        run_routing.run(config)
    config.misses_as_errors = True
    document = run_routing.run(config)
    errors = [r for r in document["records"] if r["cell"] == "error"]
    assert [r["case_id"] for r in errors] == [ids[10]] and errors[0]["error"].startswith("CassetteMiss")
    assert document["header"]["cassette_misses"] == [ids[10]]


def test_b9_and_the_ablations_from_tier_two_runs(tmp_path):
    cases = [c for c in load_cases(GOLDEN_V2_PATH) if c["category"] == "explicit"][:6]

    def document(arm, cells, model=run_routing.DEFAULT_MODEL, cross=0, later=None):
        trials = 1 + len(later or [])
        rows = [cells, *(later or [])]
        records = [_record(c, t, rows[t][i], cross_client=i < cross) for i, c in enumerate(cases) for t in range(trials)]
        for r in records:
            if r["cross_client"]:
                r["cell"], r["correct"] = "wrong_reuse", False
        return _document(arm, records, tmp_path / f"{arm}_{model}.json", set="v2", model=model, trials=trials)

    right, wrong = ["correct_reuse"] * 6, ["wrong_reuse"] * 6
    # fix_v2's later trials miss the last two cases, so its majority differs from its first trial there.
    paths = [document("fix_v1", wrong, cross=2), document("fix_v2", right, later=[right[:4] + wrong[:2]] * 2),
             document("fix_v1", wrong, model="deepseek-v4-pro", cross=3),
             document("fix_v2", right[:5] + ["wrong_reuse"], model="deepseek-v4-pro", cross=1),
             document("fix_v2_no_c1", right), document("fix_v2_no_c3", right[:4] + wrong[:2])]
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path), "--set", "v2"]) == 0
    report = json.loads((tmp_path / "RESULTS_v2.json").read_text(encoding="utf-8"))
    verdict = {v["bar"]: v for v in report["verdicts"]}
    assert verdict["B9"]["verdict"] == "pass" and "trials 2 and 3 were cut for quota" in verdict["B9"]["detail"]
    # Ablations pair with fix_v2's first trial (all right here), not its majority (all wrong past trial 1).
    assert verdict["Ablation: no c1"]["verdict"] == "not needed on this set"
    assert verdict["Ablation: no c3"]["verdict"].startswith("needed")
    assert verdict["Ablation: no c4"]["verdict"] == "pending tier 2"
    pair = report["report"]["paired_by_key"]["fix_v2_no_c3_vs_fix_v2_minimax-m3_k8_n500"]
    assert (pair["b"], pair["c"]) == (2, 0)
