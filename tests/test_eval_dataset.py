"""The synthetic pool and golden set: deterministic, committed as generated, and shaped as the plan says."""

import json
import re

from evals import generate
from evals.audit_leakage import LIMIT, leaked_cases
from evals.generate import CATEGORY_COUNTS, load_cases, load_pool, roster_for


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_committed_files_match_the_generator():
    pool, cases = generate.generate()
    assert _lines(generate.POOL_PATH) == pool
    assert _lines(generate.GOLDEN_PATH) == cases


def test_category_counts_and_splits():
    cases = load_cases()
    counts = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    assert counts == CATEGORY_COUNTS
    heldout_templates = {c["template_id"] for c in cases if c["split"] == "heldout"}
    report_templates = {c["template_id"] for c in cases if c["split"] == "report"}
    assert heldout_templates and not heldout_templates & report_templates
    assert 0.25 <= len(load_cases(split="heldout")) / len(cases) <= 0.40


def test_gold_agents_exist_and_belong_to_the_case():
    pool = load_pool()
    for case in load_cases():
        gold = case["gold"]
        assert set(gold["agent_ids"]) <= set(case["target_ids"])
        if gold["kind"] == "create":
            assert gold["agent_ids"] == []
        else:
            assert gold["agent_ids"]
        for agent_id in case["target_ids"] + case["hard_negative_ids"]:
            assert agent_id in pool


def test_rosters_nest_and_keep_targets_in_place():
    pool = load_pool()
    for case in load_cases()[::7]:
        rosters = [[a["id"] for a in roster_for(case, n, pool=pool)] for n in (5, 50, 500, 5000)]
        for small, large in zip(rosters, rosters[1:]):
            assert set(small) <= set(large)
        assert len(rosters[1]) == 50 and len(rosters[3]) == 5000
        for roster in rosters:
            assert set(case["target_ids"] + case["hard_negative_ids"]) <= set(roster)
            assert len(set(roster)) == len(roster)
            names = [pool[i]["name"] for i in roster]
            assert len(set(names)) == len(names)


def test_target_position_is_varied():
    positions = set()
    for case in load_cases():
        roster = [a["id"] for a in roster_for(case, 50)]
        index = roster.index((case["target_ids"] or case["hard_negative_ids"])[0])
        positions.add("start" if index < 5 else "end" if index > 44 else "middle")
    assert positions == {"start", "middle", "end"}


def test_only_invented_domains():
    text = generate.POOL_PATH.read_text(encoding="utf-8") + generate.GOLDEN_PATH.read_text(encoding="utf-8")
    domains = set(re.findall(r"@([\w.-]+)", text))
    assert domains
    assert all(d.endswith((".example.com", ".example.ca", ".test")) or d in {"example.com", "example.ca"}
               for d in domains), domains


def test_name_leak_is_under_the_limit():
    cases = load_cases()
    assert len(leaked_cases(cases, load_pool())) / len(cases) < LIMIT
