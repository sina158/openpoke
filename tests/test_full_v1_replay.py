"""Opt-in (`pytest -m slow`): every round-one fix-arm run replays in full from its cassette under profile v1.

The quick version (tests/test_v1_profile_replays.py) replays five cases; this one replays each committed run of
the fix arm at k=8 on minimax-m3, every case, size and trial, and checks each cell against the recorded one.
"""

import json
import os
import shutil

import pytest

from evals import run_routing
from evals.cassette import CASSETTE_DIR
from evals.run_routing import RESULTS_DIR, RunConfig

FIX_CASSETTE = "routing_fix_minimax-m3_k8.jsonl"
RUNS = sorted(path for path in RESULTS_DIR.glob("*_fix_minimax-m3_k8_*.json") if "_partial_" not in path.name)


@pytest.mark.slow
@pytest.mark.parametrize("recorded_run", RUNS, ids=[path.name for path in RUNS])
def test_a_round_one_fix_run_replays_in_full_under_v1(recorded_run, tmp_path, monkeypatch):
    shutil.copy(CASSETTE_DIR / FIX_CASSETTE, tmp_path / FIX_CASSETTE)
    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v1")
    recorded = json.loads(recorded_run.read_text(encoding="utf-8"))
    header = recorded["header"]
    config = RunConfig(arm="fix", model=header["model"], sizes=tuple(header["sizes"]), trials=header["trials"],
                       split=header.get("split", "all"), k=header["k"], temperature=header["temperature"],
                       seed=header["seed"], mode="replay", cassette_dir=tmp_path,
                       render_workers=min(8, os.cpu_count() or 1))

    document = run_routing.run(config)  # raises CassetteMiss on any unrecorded call

    assert document["header"]["live_calls"] == 0
    assert len(document["records"]) == len(recorded["records"])

    def cells(records):
        return {(r["case_id"], r["size"], r["trial"]): r["cell"] for r in records}

    assert cells(document["records"]) == cells(recorded["records"])
