"""Result summaries: rates with their denominators, the split rule, clustering, pairing and consistency."""

import json

import pytest

from evals import compare, run_routing


def _record(case_id, trial, cell, kind="reuse", size=500, cross=False, tokens=1000, reused=(), category="explicit",
            asked=None, split="report", template="explicit.t1", **extra):
    resolved = [{"action": "clarify", "agent_ids": []}] if "clarify" in cell else []
    resolved += [{"action": "reuse", "agent_id": agent_id} for agent_id in reused]
    return {"case_id": case_id, "category": category, "template_id": template, "split": split, "trial": trial,
            "size": size, "cell": cell, "gold": {"kind": kind, "agent_ids": ["g_1"] if kind == "reuse" else []},
            "correct": cell in run_routing.CORRECT_CELLS, "cross_client": cross, "resolved": resolved,
            "user_messages": [asked] if asked else [], "prompt_tokens": tokens, "latency_ms": 100.0,
            "cost_usd": 0.001, **extra}


def _document(arm, records, path, split="report", trials=2, **header):
    header = {"arm": arm, "model": "m", "k": 8, "date": "2026-10-28T00:00:00Z", "commit": "abc1234",
              "split": split, "sizes": [500], "trials": trials, **header}
    path.write_text(json.dumps({"header": header, "records": records}), encoding="utf-8")
    return path


def _base():
    return [_record("a", t, "correct_reuse") for t in (0, 1)] + [
        _record("b", 0, "wrong_reuse", cross=True, reused=["g_1", "g_9"], template="entity.t1"),
        _record("b", 1, "wrong_reuse", cross=True, reused=["g_9"], template="entity.t1"),
    ] + [_record("c", t, "hijack", kind="create", category="should_create", cross=True, template="should_create.t1")
         for t in (0, 1)] + [
        _record("d", t, "no_route", asked="Which one do you mean?" if t else "On it.") for t in (0, 1)]


def _fix():
    return [_record("a", t, "correct_reuse", tokens=300) for t in (0, 1)] + [
        _record("b", 0, "correct_reuse", tokens=300, template="entity.t1"),
        _record("b", 1, "duplicate", tokens=300, template="entity.t1"),
    ] + [_record("c", t, "correct_create", kind="create", tokens=300, category="should_create",
                 template="should_create.t1") for t in (0, 1)] + [
        _record("d", 0, "correct_reuse", tokens=300, guard_fired=True, guard_outcome="reused_suggested",
                target_archived=True, search_found_target=True),
        _record("d", 1, "correct_reuse", tokens=300)]


def test_compare_reports_rates_pairs_and_consistency(tmp_path):
    paths = [_document("baseline", _base(), tmp_path / "b.json"), _document("fix", _fix(), tmp_path / "f.json")]
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path)]) == 0

    report = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))["report"]
    rows = {row["arm"]: row for row in report["groups"]}
    base, fix = rows["baseline"], rows["fix"]
    assert base["accuracy"]["k"] == 2 and fix["accuracy"]["k"] == 7
    assert base["accuracy"]["clustered"]["templates"] == 3
    lo, hi = fix["accuracy"]["clustered"]["ci95"]
    assert 0 <= lo <= 7 / 8 <= hi <= 1
    assert base["wrong_reuse"]["rate"] == pytest.approx(2 / 6)
    assert base["spray"]["k"] == 1 and base["wrong_only"]["k"] == 1
    assert base["cross_client"]["wrong_reuse"] == 2 and base["cross_client"]["hijack"] == 2
    assert fix["cross_client"]["wrong_reuse_upper95"] == pytest.approx(3 / 6)
    assert base["no_route_questions"] == 1
    assert fix["by_category"]["explicit"]["k"] == 5 and fix["by_category"]["explicit"]["templates"] == 2
    assert fix["guard"] == {"fired": 1, "outcomes": {"reused_suggested": 1}, "correct_after": 1}
    assert fix["archived_target"]["archived"]["n"] == 1 and fix["archived_target"]["search_found_target"] == 1
    assert fix["pass_k"] == pytest.approx(3 / 4) and fix["agreement"] == pytest.approx(3 / 4)
    assert report["by_key"]["fix_m_k8_n500"]["n"] == 8
    (pair,) = report["paired"]
    # Per case on the majority over two trials: b is a 1-1 tie (not a majority), so only c and d flip.
    assert (pair["pairs"], pair["b"], pair["c"]) == (4, 0, 2)
    assert pair["mcnemar_p"] == pytest.approx(0.5) and pair["templates"] == 3
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "## Headline: report split" in markdown and "template bootstrap" in markdown
    assert "### Duplicate guard" in markdown and "1 of 2 no-route turns asked the user" in markdown


def test_headline_uses_the_report_split_of_a_full_run(tmp_path):
    records = _base() + [_record("h", t, "wrong_reuse", split="heldout", reused=["g_9"]) for t in (0, 1)]
    path = _document("baseline", records, tmp_path / "all.json", split="all")
    assert compare.main([str(path), "--out-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    assert report["report"]["groups"][0]["n"] == 8 and report["all"]["groups"][0]["n"] == 10


def test_partial_runs_are_skipped_and_duplicates_refused(tmp_path):
    full = _document("baseline", _base(), tmp_path / "full.json")
    smoke = _document("baseline", _base()[:2], tmp_path / "smoke.json", limit=5)
    assert compare.main([str(full), str(smoke), "--out-dir", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    assert report["report"]["groups"][0]["n"] == 8 and report["skipped_partial_runs"] == ["smoke.json"]

    again = _document("baseline", _base(), tmp_path / "again.json")
    assert compare.main([str(full), str(again), "--out-dir", str(tmp_path / "x")]) == 1
    rerun = _document("baseline", _base(), tmp_path / "rerun.json", trials=1)
    assert compare.main([str(full), str(rerun), "--out-dir", str(tmp_path / "y")]) == 1


def test_accuracy_without_ambiguous_and_paraphrase(tmp_path):
    records = [_record("a", 0, "correct_reuse"), _record("b", 0, "wrong_reuse", reused=["g_9"]),
               _record("p", 0, "wrong_reuse", reused=["g_9"], category="paraphrase", template="paraphrase.t1"),
               _record("q", 0, "correct_reuse", category="paraphrase", template="paraphrase.t1"),
               _record("m", 0, "no_route", kind="clarify", category="ambiguous", template="ambiguous.t1")]
    path = _document("baseline", records, tmp_path / "b.json", split="all", trials=1)
    assert compare.main([str(path), "--out-dir", str(tmp_path)]) == 0

    report = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    for table in ("report", "all"):
        row = report[table]["by_key"]["baseline_m_k8_n500"]
        assert row["accuracy"]["n"] == 5
        fair = row["accuracy_excl_ambiguous_paraphrase"]
        assert (fair["k"], fair["n"], fair["rate"]) == (1, 2, 0.5) and len(fair["ci95"]) == 2
    assert "without ambiguous, paraphrase" in (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
