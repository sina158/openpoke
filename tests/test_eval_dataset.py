"""The synthetic pool and golden set: deterministic, committed as generated, and shaped as the plan says."""

import hashlib
import json
import re
from collections import Counter, defaultdict

import pytest

from evals import generate
from evals.audit_leakage import LIMIT, leaked_cases
from evals.generate import CATEGORY_COUNTS, CATEGORY_COUNTS_V2, load_cases, load_pool, roster_for


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


# ---- the round-two set: generated blind, locked by hash, never tuned on ------------------------------------

V2_LOCK = generate.GOLDEN_V2_PATH.with_suffix(".sha256")
# The held-out set: the same generator on a new seed, for measuring changes made after reading v2's failures.
HELDOUT_SEED = 20261030
HELDOUT_PATHS = generate.set_paths(2, "heldout")


def _v2(name=None):
    pool_path, golden_path = generate.set_paths(2, name)
    return load_cases(golden_path), load_pool(pool_path)


def test_v2_committed_files_match_the_generator():
    pool, cases = generate.generate(version=2)
    assert _lines(generate.POOL_V2_PATH) == pool
    assert _lines(generate.GOLDEN_V2_PATH) == cases


def test_main_reproduces_the_committed_bytes_and_writes_a_named_set_beside_them(tmp_path, monkeypatch):
    names = ("POOL_PATH", "GOLDEN_PATH", "POOL_V2_PATH", "GOLDEN_V2_PATH")
    committed = {name: getattr(generate, name) for name in names}
    monkeypatch.setattr(generate, "EVALS_DIR", tmp_path)
    for name, path in committed.items():
        monkeypatch.setattr(generate, name, tmp_path / path.parent.name / path.name)
    assert generate.main([]) == 0 and generate.main(["--version", "2"]) == 0
    assert generate.main(["--version", "2", "--seed", str(HELDOUT_SEED), "--name", "heldout"]) == 0
    for path in [*committed.values(), *HELDOUT_PATHS]:  # the named run left the default files as they were
        assert (tmp_path / path.parent.name / path.name).read_bytes() == path.read_bytes(), path.name


def test_heldout_is_a_new_draw_with_the_v2_mix():
    (cases, pool), (v2_cases, v2_pool) = _v2("heldout"), _v2()
    assert Counter(c["category"] for c in cases) == CATEGORY_COUNTS_V2
    assert Counter(c["gold"]["kind"] for c in cases) == Counter(c["gold"]["kind"] for c in v2_cases)
    assert {c["split"] for c in cases} == {"report"} and len(pool) == len(v2_pool)
    assert len({c["message"] for c in cases} & {c["message"] for c in v2_cases}) < len(cases) // 10


def test_v2_set_matches_its_lock():
    digest = hashlib.sha256(generate.GOLDEN_V2_PATH.read_bytes()).hexdigest()
    assert V2_LOCK.read_text(encoding="utf-8").split()[0] == digest


def test_v2_counts_templates_and_splits():
    cases, _ = _v2()
    assert Counter(c["category"] for c in cases) == CATEGORY_COUNTS_V2
    assert {c["split"] for c in cases} == {"report"}
    templates = defaultdict(set)
    for case in cases:
        templates[case["category"]].add(case["template_id"])
    assert {k: len(v) for k, v in templates.items()} == {
        k: 3 if k == "near_duplicate" else 2 for k in CATEGORY_COUNTS_V2}
    ids = [c["case_id"] for c in cases]
    assert len(set(ids)) == len(ids)
    assert not set(ids) & {c["case_id"] for c in load_cases()}


@pytest.mark.parametrize("name", [None, "heldout"])
def test_v2_gold_agents_exist_and_belong_to_the_case(name):
    cases, pool = _v2(name)
    for case in cases:
        gold = case["gold"]
        assert set(gold["agent_ids"]) <= set(case["target_ids"])
        assert (gold["agent_ids"] == []) == (gold["kind"] == "create")
        for agent_id in case["target_ids"] + case["hard_negative_ids"]:
            assert agent_id in pool


@pytest.mark.parametrize("name", [None, "heldout"])
def test_v2_discriminators_sit_in_the_gold_agent_record(name):
    cases, pool = _v2(name)
    checked = 0
    for case in cases:
        kind, gold, terms = case["gold"]["kind"], case["gold"]["agent_ids"], case["discriminators"]
        assert bool(terms) == (kind != "clarify"), case["case_id"]
        if kind not in ("reuse", "parallel"):
            continue
        pairs = [(gold[0], term) for term in terms] if kind == "reuse" else list(zip(gold, terms))
        for agent_id, term in pairs:
            record = " ".join(str(pool[agent_id][k]) for k in ("name", "first_request", "last_response"))
            assert term in record, (case["case_id"], term)
            checked += 1
    assert checked > 80


def test_v2_clients_are_new():
    filler_surnames = {h + t for h in generate.FILLER_SURNAME_HEADS for t in generate.FILLER_SURNAME_TAILS}
    assert not set(generate.CASE_SURNAMES_V2) & (set(generate.CASE_SURNAMES) | filler_surnames)
    assert not set(generate.CASE_STEMS_V2) & (set(generate.CASE_STEMS) | set(generate.FILLER_STEMS))
    assert not set(generate.CASE_CITIES_V2) & (set(generate.CASE_CITIES) | set(generate.FILLER_CITIES))
    assert not set(generate.OCCUPATIONS_V2) & set(generate.OCCUPATIONS)
    cases, pool = _v2()
    case_text = " ".join(pool[i]["name"] for c in cases for i in c["target_ids"] + c["hard_negative_ids"])
    assert not [s for s in generate.CASE_SURNAMES + generate.CASE_STEMS if re.search(rf"\b{s}\b", case_text)]


def test_v2_policy_line_matches_the_agent_name():
    pattern = re.compile(r" (auto|home) (quote|renewal|endorsement|COI|claim|cancellation|billing)\b")
    prefix = {"auto": "AU-", "home": "HO-"}

    def mismatches(pool):
        named = [(m.group(1), a) for a in pool.values() if (m := pattern.search(a["name"]))]
        return sum(prefix[line] not in a["first_request"] for line, a in named), len(named)

    v2_bad, v2_named = mismatches(load_pool(generate.POOL_V2_PATH))
    assert v2_named > 1000 and v2_bad == 0
    v1_bad, _ = mismatches(load_pool())
    assert v1_bad > 0  # round one keeps the bug so its files regenerate unchanged


def test_v2_rosters_nest_and_keep_targets_in_place():
    cases, pool = _v2()
    positions = set()
    for case in cases[::5]:
        rosters = [[a["id"] for a in roster_for(case, n, seed=generate.SEED_V2, pool=pool)]
                   for n in (5, 50, 500, 5000)]
        for small, large in zip(rosters, rosters[1:]):
            assert set(small) <= set(large)
        assert len(rosters[1]) == 50 and len(rosters[3]) == 5000
        for roster in rosters:
            assert set(case["target_ids"] + case["hard_negative_ids"]) <= set(roster)
            names = [pool[i]["name"] for i in roster]
            assert len(set(names)) == len(names) == len(set(roster))
        index = rosters[1].index((case["target_ids"] or case["hard_negative_ids"])[0])
        positions.add("start" if index < 5 else "end" if index > 44 else "middle")
    assert positions == {"start", "middle", "end"}


@pytest.mark.parametrize("name", [None, "heldout"])
def test_v2_only_invented_domains(name):
    text = "".join(path.read_text(encoding="utf-8") for path in generate.set_paths(2, name))
    domains = set(re.findall(r"@([\w.-]+)", text))
    assert domains
    assert all(d.endswith((".example.com", ".example.ca", ".test")) or d in {"example.com", "example.ca"}
               for d in domains), domains


@pytest.mark.parametrize("name", [None, "heldout"])
def test_v2_name_leak_is_under_the_limit(name):
    cases, pool = _v2(name)
    assert len(leaked_cases(cases, pool)) / len(cases) < LIMIT


def test_v2_hand_check_sample_is_listed_in_labels():
    labels = (generate.EVALS_DIR / "golden" / "LABELS.md").read_text(encoding="utf-8")
    sample = generate.hand_check_sample(_v2()[0])
    assert len(set(sample)) == 20
    assert all(f"| `{case_id}` |" in labels for case_id in sample)
