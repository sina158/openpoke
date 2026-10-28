"""The duplicate-threshold sweep counts both mistakes and picks the fewest."""

from evals.tune_duplicate_threshold import choose, sweep


def test_sweep_counts_missed_and_false_merges():
    rows = sweep({"same_matter": [0.9, 0.6], "new_matter": [0.7], "distractor": [0.55]})
    by_t = {row["threshold"]: row for row in rows}
    assert (by_t[0.5]["missed_merges"], by_t[0.5]["false_merges"]) == (0, 2)
    assert (by_t[0.6]["missed_merges"], by_t[0.6]["false_merges"]) == (0, 1)
    assert (by_t[0.75]["missed_merges"], by_t[0.75]["false_merges"]) == (1, 0)
    assert choose(rows)["threshold"] == 0.9  # 1 mistake from 0.75 to 0.9; ties go to the higher threshold
