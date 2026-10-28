"""The baseline arm must still be the upstream request: its committed cassette replays with no misses.

A changed prompt, tool list or rendering changes the cassette key, so a miss here means the baseline
arm no longer sends what upstream sent.
"""

from evals import run_routing
from evals.run_routing import RunConfig


def test_baseline_cassette_replays_without_a_miss():
    config = RunConfig(arm="baseline", sizes=(5,), split="report", limit=5, mode="replay")
    document = run_routing.run(config)  # raises CassetteMiss on any unrecorded call

    header = document["header"]
    assert header["n_records"] == 5
    assert header["live_calls"] == 0 and header["cassette_hits"] >= 5
    assert all(record["error"] is None for record in document["records"])
