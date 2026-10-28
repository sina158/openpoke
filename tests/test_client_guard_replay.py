"""With the client guard off, round two's recordings replay exactly; with it on, only the turns it refuses change.

A recorded call is keyed by a hash of the whole request, and the guard changes no prompt, card or schema byte,
so the guard arm's first call in every case is fix_v2's recorded call. A turn the guard refuses then needs a
call fix_v2 never made, so it misses unless the guard arm's own one-trial run recorded it (trial 1); every other
turn grades exactly as fix_v2's did. The opt-in slow test replays every round-two result file in full, the
held-out runs included.
"""

import json
import os

import pytest

from evals import run_routing
from evals.cassette import CASSETTE_DIR
from evals.generate import SEED_V2
from evals.run_routing import RESULTS_DIR, RunConfig

FIX_V2_RUN = RESULTS_DIR / "20261028_v2_fix_v2_minimax-m3_k8_report_n500_t3_88f9fd2.json"
# The guard arm's own run on this set (one trial, the non-blind row): it recorded trial 1's refused turns.
GUARD_RUN = RESULTS_DIR / "20261028_v2_fix_v2_guard_minimax-m3_k8_report_n500_t1_681fc59.json"
# Two-client sprays on a paraphrase and on a shared first name, the parallel pair whose described client the
# guard refuses, a shared-client second round, and an agent message.
CASES = ["v2-paraphrase-003", "v2-ambiguous-010", "v2-parallel-002", "v2-explicit-008", "v2-agent_message-002"]
GUARDED = {(case_id, trial) for case_id in CASES[:3] for trial in range(3)}
RUNS = sorted(RESULTS_DIR.glob("20261028_v2_*.json")) + sorted((RESULTS_DIR / "heldout").glob("20261028_*.json"))


def _cells(records):
    return {(r["case_id"], r["size"], r["trial"]): r["cell"] for r in records}


def _replay(arm, monkeypatch, header=None, **config):
    monkeypatch.setenv("OPENPOKE_CLIENT_GUARD", "1")  # the arm's own pin wins over the shell
    header = header or {"sizes": [500], "trials": 3, "model": run_routing.DEFAULT_MODEL}
    config = RunConfig(arm=arm, model=header["model"], sizes=tuple(header["sizes"]), trials=header["trials"],
                       case_set=header.get("set", "v2"), cases_sample=header.get("cases_sample"),
                       sample_seed=header.get("sample_seed") or SEED_V2, mode="replay",
                       render_workers=min(8, os.cpu_count() or 1), **config)
    return run_routing.run(config)  # raises CassetteMiss on an unrecorded call unless misses are errors


def _recorded(path, case_ids=None):
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    return _cells([r for r in records if case_ids is None or r["case_id"] in case_ids])


def test_fix_v2_replays_as_recorded_with_the_guard_switched_on_in_the_shell(monkeypatch):
    document = _replay("fix_v2", monkeypatch, case_ids=CASES)
    assert document["header"]["live_calls"] == 0 and document["header"]["openpoke_client_guard"] == "0"
    assert _cells(document["records"]) == _recorded(FIX_V2_RUN, CASES)


def test_the_guard_arm_differs_from_fix_v2_only_on_the_turns_it_refuses(monkeypatch):
    # Over this handful of cases most turns miss on purpose; that is the point here, not a provider failure.
    monkeypatch.setattr(run_routing, "MAX_ERROR_SHARE", 1.0)
    monkeypatch.setattr(run_routing, "FIRST_CALLS_CHECKED", len(CASES) * 3 + 1)
    document = _replay("fix_v2_guard", monkeypatch, case_ids=CASES, misses_as_errors=True)
    records = document["records"]
    assert document["header"]["live_calls"] == 0 and document["header"]["openpoke_client_guard"] == "1"
    assert {(r["case_id"], r["trial"]) for r in records if r["client_guard_refused"]} == GUARDED
    missed = {(r["case_id"], r["trial"]) for r in records if (r["error"] or "").startswith("CassetteMiss")}
    assert missed == {(case_id, trial) for case_id, trial in GUARDED if trial}
    first = {k: v for k, v in _cells(records).items() if k[::2] in GUARDED and not k[2]}
    assert first == _recorded(GUARD_RUN, CASES[:3])
    recorded = _recorded(FIX_V2_RUN, CASES)
    assert {k: v for k, v in _cells(records).items() if k[::2] not in GUARDED} == {
        k: v for k, v in recorded.items() if k[::2] not in GUARDED}


@pytest.mark.slow
@pytest.mark.parametrize("recorded_run", RUNS, ids=[path.name for path in RUNS])
def test_every_round_two_run_replays_in_full_with_the_guard_switched_on_in_the_shell(recorded_run, monkeypatch):
    header = json.loads(recorded_run.read_text(encoding="utf-8"))["header"]
    # The two held-out arms were recorded at the same time, each with its own first call for every case, so each
    # replays from its own directory.
    mine = {"cassette_dir": CASSETTE_DIR / "heldout" / header["arm"]} if header.get("set") == "v2_heldout" else {}
    misses = bool(header.get("misses_as_errors"))
    document = _replay(header["arm"], monkeypatch, header, misses_as_errors=misses, **mine)
    assert document["header"]["live_calls"] == 0
    assert document["header"]["cassette_misses"] == (header.get("cassette_misses") or [])
    assert _cells(document["records"]) == _recorded(recorded_run)
