"""Result summaries: rates with their denominators, paired deltas and trial consistency."""

import json

import pytest

from evals import run_routing


def _record(case_id, trial, cell, kind="reuse", size=500, cross=False, tokens=1000):
    resolved = [{"action": "clarify", "agent_ids": []}] if "clarify" in cell else []
    return {"case_id": case_id, "trial": trial, "size": size, "cell": cell, "gold": {"kind": kind},
            "correct": cell in run_routing.CORRECT_CELLS, "cross_client": cross, "resolved": resolved,
            "prompt_tokens": tokens, "latency_ms": 100.0, "cost_usd": 0.001}


def _document(arm, records, path):
    header = {"arm": arm, "model": "m", "k": 8, "date": "2026-10-28T00:00:00Z", "commit": "abc1234"}
    path.write_text(json.dumps({"header": header, "records": records}), encoding="utf-8")
    return path


def test_compare_reports_rates_pairs_and_consistency(tmp_path):
    from evals import compare

    base = [_record("a", t, "correct_reuse") for t in (0, 1)] + \
           [_record("b", t, "wrong_reuse", cross=True) for t in (0, 1)] + \
           [_record("c", t, "hijack", kind="create") for t in (0, 1)]
    fix = [_record("a", t, "correct_reuse", tokens=300) for t in (0, 1)] + \
          [_record("b", 0, "correct_reuse", tokens=300), _record("b", 1, "duplicate", tokens=300)] + \
          [_record("c", t, "correct_create", kind="create", tokens=300) for t in (0, 1)]
    paths = [_document("baseline", base, tmp_path / "b.json"), _document("fix", fix, tmp_path / "f.json")]
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path)]) == 0

    report = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    rows = {row["arm"]: row for row in report["groups"]}
    assert rows["baseline"]["accuracy"]["k"] == 2 and rows["fix"]["accuracy"]["k"] == 5
    assert rows["baseline"]["wrong_reuse"]["rate"] == pytest.approx(0.5)
    assert rows["baseline"]["cross_client"]["count"] == 2
    assert rows["fix"]["cross_client"]["upper95"] == pytest.approx(0.5)
    assert rows["fix"]["duplicate"]["k"] == 1 and rows["fix"]["correct_create"]["rate"] == 1.0
    assert rows["baseline"]["hijack"] == {"count": 2, "n": 2}
    assert rows["fix"]["pass_k"] == pytest.approx(2 / 3) and rows["fix"]["agreement"] == pytest.approx(2 / 3)
    assert rows["fix"]["prompt_tokens"]["mean"] == 300
    assert rows["fix"]["usd_per_100_turns"] == pytest.approx(0.1)
    (pair,) = report["paired"]
    assert (pair["pairs"], pair["b"], pair["c"]) == (6, 0, 3)
    assert pair["mcnemar_p"] == pytest.approx(0.25)
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "| m | 500 | fix | 8 | 6 |" in markdown and "McNemar" in markdown and "pass^k" in markdown
