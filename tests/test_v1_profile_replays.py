"""Profile v1 is round one, byte for byte: the fix arm's committed cassette replays under it with no misses.

A recorded call is keyed by a hash of the whole request (system prompt, history, shortlist cards, tool
schemas), so a prompt, card or schema byte that moved under profile v1 raises CassetteMiss here. The cases
cover what the v2 changes touch: a near-duplicate pair told apart only past the old clip, an archived
target, a duplicate-guard second round, an agent message and searches, at every roster size.
"""

import json
import re
import shutil

from evals import run_routing
from evals.cassette import CASSETTE_DIR
from evals.generate import load_cases
from evals.run_routing import RESULTS_DIR, RunConfig
from server.config import get_settings

FIX_CASSETTE = "routing_fix_minimax-m3_k8.jsonl"
RECORDED_RUN = RESULTS_DIR / "20261028_fix_minimax-m3_k8_all_773a8ac.json"
CASES = ["near_duplicate-006", "near_duplicate-016", "should_create-005", "agent_message-004", "explicit-007"]
SIZES = (5, 50, 500)


def test_fix_cassette_replays_under_the_v1_profile(tmp_path, monkeypatch):
    # Only this file is loaded, so every hit comes from the cassette the claim is about.
    shutil.copy(CASSETTE_DIR / FIX_CASSETTE, tmp_path / FIX_CASSETTE)
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v1")
    config = RunConfig(arm="fix", sizes=SIZES, split="all", case_ids=CASES, mode="replay", cassette_dir=tmp_path)

    document = run_routing.run(config)  # raises CassetteMiss on any unrecorded call

    header = document["header"]
    assert header["n_records"] == len(CASES) * len(SIZES)
    assert header["live_calls"] == 0 and header["cassette_hits"] >= header["n_records"]
    recorded = json.loads(RECORDED_RUN.read_text(encoding="utf-8"))["records"]
    expected = {(r["case_id"], r["size"]): r["cell"] for r in recorded if r["case_id"] in CASES}
    assert {(r["case_id"], r["size"]): r["cell"] for r in document["records"]} == expected


def render_fix(case_id, size, profile, sandbox, monkeypatch):
    """Render one case the way the fix arm does, under ``profile``; returns the user message content."""
    case = next(c for c in load_cases() if c["case_id"] == case_id)
    for key, value in run_routing._arm_env(RunConfig(arm="fix", model=run_routing.DEFAULT_MODEL)).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", profile)
    get_settings.cache_clear()
    return run_routing.render_case(case, size, sandbox / profile).messages[0]["content"]


def shortlist_of(content):
    return content[content.index("<active_agents") : content.index("</active_agents>")]


def test_the_replay_would_see_a_card_change(fresh_stores, tmp_path, monkeypatch):
    # Negative control: v2 cards keep the city, so this case's v2 prompt is not in the v1 cassette.
    v1, v2 = (shortlist_of(render_fix("near_duplicate-006", 500, p, tmp_path, monkeypatch)) for p in ("v1", "v2"))
    assert "Barrie" not in v1 and "Sarnia" not in v1
    assert "contractor in Barrie." in v2 and "contractor in Sarnia." in v2


def test_the_replay_would_see_an_archived_candidate(fresh_stores, tmp_path, monkeypatch):
    # "pull the terms we agreed for Hollowtree Dental Clinic last year": the target is the 2025 renewal,
    # archived 350 days ago. v1 hides it; v2 shows it second, behind the 2026 renewal, with its status.
    v1, v2 = (shortlist_of(render_fix("near_duplicate-016", 500, p, tmp_path, monkeypatch)) for p in ("v1", "v2"))
    assert "renewal 2025" not in v1 and 'total="251"' in v1
    names = re.findall(r'name="([^"]+)" status="(\w+)"', v2)
    assert names[:2] == [("Hollowtree Dental Clinic renewal 2026", "active"),
                         ("Hollowtree Dental Clinic renewal 2025", "archived")]
    assert 'shown="8" total="500"' in v2 and "492 more agents are not shown" in v2
