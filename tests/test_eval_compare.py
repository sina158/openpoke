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


def _model_runs(tmp_path):
    base = [_record(c, 0, "correct_reuse", tokens=1000, llm_calls=1, searches=0) for c in "abcd"]
    fix = [_record(c, 0, "correct_reuse", tokens=600, llm_calls=2, searches=1) for c in "abc"]
    fix.append(_record("d", 0, "correct_reuse", tokens=600, llm_calls=2, searches=1, cost_usd=None))
    return (_document("baseline", base, tmp_path / "b.json", trials=1, model="minimax-m3"),
            _document("fix", fix, tmp_path / "f.json", trials=1, model="minimax-m3"))


def test_spend_calls_per_turn_and_token_bars(tmp_path):
    paths = _model_runs(tmp_path)
    assert compare.main([str(p) for p in paths] + ["--out-dir", str(tmp_path)]) == 0

    results = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    assert results["spend"]["total_usd"] == pytest.approx(0.007)
    assert results["spend"]["unpriced_records"] == 1 and results["spend"]["files"] == 2
    fix = results["report"]["by_key"]["fix_minimax-m3_k8_n500"]
    assert fix["llm_calls"]["mean"] == 2 and fix["searches"]["mean"] == 1
    assert fix["prompt_tokens"]["mean"] == 600 and fix["prompt_tokens_per_call"]["mean"] == 300
    assert results["bars"]["prompt_token_ratio_n500"] == pytest.approx(0.6)
    assert results["bars"]["prompt_token_per_call_ratio_n500"] == pytest.approx(0.3)
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "fix / baseline at N=500: 60.0% per turn, 30.0% per LLM call" in markdown
    assert "$0.0070" in markdown


def test_token_bars_are_none_without_both_arms(tmp_path):
    base, _ = _model_runs(tmp_path)
    old = _document("prompt_only", [_record("a", 0, "correct_reuse")], tmp_path / "o.json", trials=1)
    assert compare.main([str(base), str(old), "--out-dir", str(tmp_path)]) == 0

    results = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    assert results["bars"] == {"prompt_token_ratio_n500": None, "prompt_token_per_call_ratio_n500": None}
    # A file written before the call counts were recorded: no mean rather than a made-up zero.
    row = results["report"]["by_key"]["prompt_only_m_k8_n500"]
    assert row["llm_calls"]["mean"] is None and row["searches"]["mean"] is None
    assert "fix / baseline at N=500" not in (tmp_path / "RESULTS.md").read_text(encoding="utf-8")


def test_injection_summary_is_carried_into_the_results(tmp_path):
    injection = {"attacks": 46, "unauthorised_sends": 0, "positive_controls": 4, "positive_controls_sent": 4,
                 "upper95": 3 / 46}
    (tmp_path / "INJECTION.json").write_text(json.dumps(injection), encoding="utf-8")
    path = _document("baseline", _base(), tmp_path / "b.json")
    assert compare.main([str(path), "--out-dir", str(tmp_path)]) == 0

    assert json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))["injection"] == injection
    markdown = (tmp_path / "RESULTS.md").read_text(encoding="utf-8")
    assert "46 attacks, 0 sends without a consumed approval (95% upper bound 6.5%" in markdown


def test_default_inputs_are_the_top_level_routing_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "RESULTS_DIR", tmp_path)
    _document("baseline", _base(), tmp_path / "20261028_baseline_m_k8_report_abc1234.json")
    for summary in ("RESULTS.json", "DUPLICATE_THRESHOLD.json"):
        (tmp_path / summary).write_text(json.dumps({"source": "x", "sweep": []}), encoding="utf-8")
    injection = {"attacks": 2, "unauthorised_sends": 0, "positive_controls": 1, "positive_controls_sent": 1,
                 "upper95": 1.0}
    (tmp_path / "INJECTION.json").write_text(json.dumps(injection), encoding="utf-8")
    (tmp_path / "upstream").mkdir()
    _document("baseline", _base(), tmp_path / "upstream" / "20261028_baseline_m_k8_report_def5678.json")
    assert compare.main([]) == 0

    results = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    assert [row["file"] for row in results["report"]["groups"]] == ["20261028_baseline_m_k8_report_abc1234.json"]
    assert results["injection"] == injection


def test_report_row_prefers_the_run_with_more_trials(tmp_path):
    one = _document("baseline", [_record(c, 0, "correct_reuse") for c in "ab"], tmp_path / "t1.json", split="all",
                    trials=1)
    three = _document("baseline", [_record(c, t, "wrong_reuse", reused=["g_9"]) for c in "ab" for t in range(3)],
                      tmp_path / "t3.json", trials=3)
    for order in ([one, three], [three, one]):
        assert compare.main([str(p) for p in order] + ["--out-dir", str(tmp_path)]) == 0
        results = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
        report_row, all_row = (results[table]["by_key"]["baseline_m_k8_n500"] for table in ("report", "all"))
        assert (report_row["file"], report_row["trials"], report_row["n"]) == ("t3.json", 3, 6)
        assert (all_row["file"], all_row["trials"], all_row["n"]) == ("t1.json", 1, 2)

    tie = _document("baseline", [_record(c, t, "correct_reuse") for c in "ab" for t in range(3)],
                    tmp_path / "tie.json", split="all", trials=3)
    assert compare.main([str(three), str(tie), "--out-dir", str(tmp_path / "x")]) == 1


def test_the_full_set_of_runs_compares_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "RESULTS_DIR", tmp_path)

    def run(arm, split="report", trials=1, k=8, model="m"):
        records = [_record(c, t, "correct_reuse", split=s) for c, s in (("a", "report"), ("h", "heldout"))
                   for t in range(trials) if split == "all" or s == split]
        _document(arm, records, tmp_path / f"20261028_{arm}_{model}_k{k}_{split}_t{trials}.json", split=split,
                  trials=trials, k=k, model=model)

    for arm in ("baseline", "prompt_only", "fix", "cards_full"):
        run(arm, split="all")
    run("baseline", trials=3)
    run("fix", trials=3)
    run("fix", k=3)
    run("fix", k=16)
    run("baseline", model="m2")
    run("fix", model="m2")
    assert compare.main([]) == 0

    results = json.loads((tmp_path / "RESULTS.json").read_text(encoding="utf-8"))
    report, everything = results["report"], results["all"]
    assert report["by_key"]["baseline_m_k8_n500"]["trials"] == 3 and report["by_key"]["fix_m_k8_n500"]["trials"] == 3
    assert {"fix_m_k3_n500", "fix_m_k16_n500", "fix_m2_k8_n500", "cards_full_m_k8_n500"} <= set(report["by_key"])
    assert "fix_vs_baseline_m2_k8_n500" in report["paired_by_key"]
    assert everything["by_key"]["baseline_m_k8_n500"]["trials"] == 1 and len(everything["groups"]) == 4
