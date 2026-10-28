"""Result summaries: rates with their denominators, paired deltas and trial consistency."""

import json

import pytest

from evals import run_routing


def _record(case_id, trial, cell, kind="reuse", size=500, cross=False, tokens=1000, reused=(), category="explicit",
            asked=None):
    resolved = [{"action": "clarify", "agent_ids": []}] if "clarify" in cell else []
    resolved += [{"action": "reuse", "agent_id": agent_id} for agent_id in reused]
    return {"case_id": case_id, "category": category, "trial": trial, "size": size, "cell": cell,
            "gold": {"kind": kind, "agent_ids": ["g_1"] if kind == "reuse" else []},
            "correct": cell in run_routing.CORRECT_CELLS, "cross_client": cross, "resolved": resolved,
            "user_messages": [asked] if asked else [], "prompt_tokens": tokens, "latency_ms": 100.0,
            "cost_usd": 0.001}


def _document(arm, records, path):
    header = {"arm": arm, "model": "m", "k": 8, "date": "2026-10-28T00:00:00Z", "commit": "abc1234"}
    path.write_text(json.dumps({"header": header, "records": records}), encoding="utf-8")
    return path


def test_compare_reports_rates_pairs_and_consistency(tmp_path):
    from evals import compare

    base = [_record("a", t, "correct_reuse") for t in (0, 1)] + [
        _record("b", 0, "wrong_reuse", cross=True, reused=["g_1", "g_9"]),
        _record("b", 1, "wrong_reuse", cross=True, reused=["g_9"]),
    ] + [_record("c", t, "hijack", kind="create", category="should_create") for t in (0, 1)] + [
        _record("d", t, "no_route", asked="Which one do you mean?" if t else "On it.") for t in (0, 1)]
    fix = [_record("a", t, "correct_reuse", tokens=300) for t in (0, 1)] + [
        _record("b", 0, "correct_reuse", tokens=300), _record("b", 1, "duplicate", tokens=300),
    ] + [_record("c", t, "correct_create", kind="create", tokens=300, category="should_create") for t in (0, 1)] + [
        _record("d", t, "correct_reuse", tokens=300) for t in (0, 1)]
    paths = [_document("baseline", base, tmp_path / "b.json"), _document("fix", fix, tmp_path / "f.json")]
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path)]) == 0

    report = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    rows = {row["arm"]: row for row in report["groups"]}
    assert rows["baseline"]["accuracy"]["k"] == 2 and rows["fix"]["accuracy"]["k"] == 7
    assert rows["baseline"]["wrong_reuse"]["rate"] == pytest.approx(2 / 6)
    assert rows["baseline"]["spray"]["k"] == 1 and rows["baseline"]["spray"]["cross_client"] == 1
    assert rows["baseline"]["wrong_only"]["k"] == 1 and rows["baseline"]["wrong_only"]["cross_client"] == 1
    assert rows["baseline"]["cross_client"]["count"] == 2
    assert rows["baseline"]["no_route_questions"] == 1
    assert rows["baseline"]["by_category"]["should_create"]["k"] == 0
    assert (rows["fix"]["by_category"]["explicit"]["k"], rows["fix"]["by_category"]["explicit"]["n"]) == (5, 6)
    assert rows["fix"]["cross_client"]["upper95"] == pytest.approx(3 / 8)
    assert rows["fix"]["duplicate"]["k"] == 1 and rows["fix"]["correct_create"]["rate"] == 1.0
    assert rows["baseline"]["hijack"] == {"count": 2, "n": 2}
    assert rows["fix"]["pass_k"] == pytest.approx(3 / 4) and rows["fix"]["agreement"] == pytest.approx(3 / 4)
    assert rows["fix"]["prompt_tokens"]["mean"] == 300
    assert rows["fix"]["usd_per_100_turns"] == pytest.approx(0.1)
    assert report["by_key"]["fix_m_k8_n500"]["n"] == 8
    (pair,) = report["paired"]
    assert (pair["pairs"], pair["b"], pair["c"]) == (8, 0, 5)
    assert pair["mcnemar_p"] == pytest.approx(0.0625)
    assert report["paired_by_key"]["fix_vs_baseline_m_k8_n500"] == pair
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "| m | 500 | fix | 8 | 8 |" in markdown and "McNemar" in markdown and "pass^k" in markdown
    assert "## Accuracy by category" in markdown and "1 of 2 no-route turns asked the user" in markdown
