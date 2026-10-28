"""Summarise routing result files into evals/results/RESULTS.md and RESULTS.json.

    python -m evals.compare                    # every full routing result file directly in evals/results
    python -m evals.compare a.json b.json      # just these
    python -m evals.compare --set v2           # the fresh set's files, into RESULTS_v2.md and RESULTS_v2.json

One report covers one golden set: a file from another set is skipped when scanning the directory and refused
when named, so a round-one row is never averaged or paired with a fresh-set row.

The headline tables use the report split (BARS.md); all 130 cases are a secondary table. Runs made with
--limit or --cases are skipped, and two files with the same configuration are refused rather than one silently
replacing the other. The one exception is the report table: a report-split run with more trials replaces the
report cases of a full run of the same configuration. Rates use the denominator that matches the question: wrong reuse and duplicates over cases
whose gold is an existing agent, correct create and hijacks over should-create cases, clarify recall over
ambiguous cases. Cases built from one template are not independent, so every group also reports its number of
templates (the effective n) and a template-clustered bootstrap interval. The injection suite's counts are
carried over from INJECTION.json (``make injection``) when it is present.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evals.generate import load_cases, load_pool
from evals.run_routing import ARCHIVE_IDLE_DAYS, CELLS, DEFAULT_MODEL, EVIDENCE_CATEGORIES, RESULTS_DIR, SETS, _model_slug
from evals.stats import agreement, cluster_bootstrap, mcnemar_exact, pass_k, rule_of_three, wilson

CATEGORIES = ("explicit", "entity", "paraphrase", "pronoun", "near_duplicate", "should_create", "ambiguous",
              "parallel", "agent_message")
# The baseline cannot win these by construction: it has no clarify tool, and paraphrase cases are built so the
# agent's name lacks the discriminator. Accuracy without them compares the arms on fair ground.
BASELINE_UNWINNABLE = ("ambiguous", "paraphrase")
TABLES = ("report", "all")
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261028
RowKey = Tuple[str, str, int, str, int, Optional[int]]  # arm, model, k, roster mode, size, cases sample
INJECTION_FILE = "INJECTION.json"
DEMO_SAMPLE = 30  # cases in the N=5,000 and cards_full demonstrations (--cases-sample)


def references(arm: str) -> List[str]:
    """The arms `arm` is paired against: the baseline; for v2 arms round one's fix too; for the v2 ablation
    and evidence arms, fix_v2 as well."""
    out = [] if arm == "baseline" else ["baseline"]
    if arm.startswith("fix_v2"):
        out.append("fix_v1")
    if arm.startswith("fix_v2_"):
        out.append("fix_v2")
    return out


class MixedResults(ValueError):
    """Two result files would feed the same row."""


def config_key(header: Dict[str, Any]) -> tuple:
    return (header["arm"], header["model"], header["k"], header.get("split", "all"), tuple(header["sizes"]),
            header.get("trials", 1), header.get("openpoke_roster_mode"), header.get("set", "v1"),
            header.get("cases_sample"))


def set_of(header: Dict[str, Any]) -> str:
    """The golden set a result file was run on; files from before the set key are round one's."""
    return header.get("set", "v1")


def load_documents(paths: Sequence[Path]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load full result files; return (documents, skipped partial files). Refuses duplicate configurations."""
    documents, skipped, seen = [], [], {}
    for path in paths:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        document["source"] = Path(path).name
        header = document["header"]
        if header.get("limit") or header.get("case_ids"):
            skipped.append(document["source"])
            continue
        key = config_key(header)
        if key in seen:
            raise MixedResults(f"{seen[key]} and {document['source']} have the same configuration; pass one")
        seen[key] = document["source"]
        documents.append(document)
    return documents, skipped


def _set_cases(case_set: str) -> List[Dict[str, Any]]:
    path = SETS[case_set][0]
    return load_cases(path) if path.exists() else []


def _template_of(case_set: str = "v1") -> Dict[str, str]:
    return {case["case_id"]: case["template_id"] for case in _set_cases(case_set)}


def _idle_targets(case_set: str = "v1") -> Dict[str, bool]:
    """Case id -> whether a gold target is idle past the archive rule at the case's clock (non-create only)."""
    cases = _set_cases(case_set)
    if not cases:
        return {}
    pool = load_pool(SETS[case_set][1])
    return {c["case_id"]: any(pool[i]["last_active_days_ago"] > ARCHIVE_IDLE_DAYS for i in c["gold"]["agent_ids"])
            for c in cases if c["gold"]["agent_ids"]}


def set_mix(case_set: str = "v1", split: str = "report") -> Dict[str, Any]:
    """The set's archived-target mix: non-create cases of the split whose gold target is idle."""
    wanted = {c["case_id"] for c in _set_cases(case_set) if split == "all" or c["split"] == split}
    idle = {case: flag for case, flag in _idle_targets(case_set).items() if case in wanted}
    return {"set": case_set, "split": split, "non_create": len(idle), "idle_target": sum(idle.values()),
            "share": sum(idle.values()) / len(idle) if idle else None}


def _template(record: Dict[str, Any], templates: Dict[str, str]) -> str:
    return record.get("template_id") or templates.get(record["case_id"], record["case_id"])


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))
    return ordered[index]


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _rate(k: int, n: int) -> Dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": [lo, hi]}


def _clustered(records: List[Dict[str, Any]], templates: Dict[str, str]) -> Dict[str, Any]:
    by_template = defaultdict(list)
    for r in records:
        by_template[_template(r, templates)].append(float(r["correct"]))
    lo, hi = cluster_bootstrap(by_template, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED)
    return {"templates": len(by_template), "ci95": [lo, hi]}


def _split_wrong_reuse(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Wrong reuses where the gold agent was also messaged (spray) and where it was not (wrong only)."""
    spray, wrong_only = [], []
    for r in records:
        if r["cell"] != "wrong_reuse":
            continue
        reused = {x.get("agent_id") for x in r["resolved"] if x["action"] == "reuse"}
        (spray if reused & set(r["gold"]["agent_ids"]) else wrong_only).append(r)
    return spray, wrong_only


def summarise(records: List[Dict[str, Any]], templates: Dict[str, str],
              idle: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """All the per-group numbers the report needs."""
    cells = {cell: sum(r["cell"] == cell for r in records) for cell in CELLS}
    gold = Counter(r["gold"]["kind"] for r in records)
    n_existing = gold["reuse"] + gold["parallel"]
    predicted_clarify = sum(any(x["action"] == "clarify" for x in r["resolved"]) for r in records)
    cross = {cell: sum(r["cell"] == cell and r["cross_client"] for r in records)
             for cell in ("wrong_reuse", "hijack", "wrong_guess")}
    spray, wrong_only = _split_wrong_reuse(records)
    tokens = [r["prompt_tokens"] for r in records]
    latency = [r["latency_ms"] for r in records]
    costs = [r["cost_usd"] for r in records if r["cost_usd"] is not None]
    fair = [r for r in records if r["category"] not in BASELINE_UNWINNABLE]
    # Older result files lack the call counts; their means stay None rather than zero.
    counted = [r for r in records if "llm_calls" in r]
    cache = [r for r in records if r.get("cache_reported")]
    # B2a (lead's ruling, set before any B2a number was read): a case whose gold itself names two clients
    # (parallel) is not a violation, so B2a counts the other cases; parallel-gold ones are shown beside it.
    two_client_gold = [r for r in records if r["gold"]["kind"] == "parallel"]
    others = [r for r in records if r["gold"]["kind"] != "parallel"]
    multi = [r for r in others if r.get("multi_client_send")]
    multi_parallel = [r for r in two_client_gold if r.get("multi_client_send")]
    summary = {
        "n": len(records),
        "cells": cells,
        "accuracy": {**_rate(sum(r["correct"] for r in records), len(records)),
                     "clustered": _clustered(records, templates)},
        "accuracy_excl_ambiguous_paraphrase": _rate(sum(r["correct"] for r in fair), len(fair)),
        "wrong_reuse": _rate(cells["wrong_reuse"], n_existing),
        "spray": {**_rate(len(spray), n_existing), "cross_client": sum(r["cross_client"] for r in spray)},
        "wrong_only": {**_rate(len(wrong_only), n_existing),
                       "cross_client": sum(r["cross_client"] for r in wrong_only)},
        "cross_client": {**cross, "wrong_reuse_n": n_existing,
                         "wrong_reuse_upper95": rule_of_three(n_existing) if cross["wrong_reuse"] == 0 else None},
        "duplicate": _rate(cells["duplicate"], n_existing),
        "correct_create": _rate(cells["correct_create"], gold["create"]),
        "hijack": {"count": cells["hijack"], "n": gold["create"],
                   "upper95": rule_of_three(gold["create"]) if cells["hijack"] == 0 else None},
        # Shared-client refusals (v2 c1): turns that ended with every routing call refused (graded no_route),
        # and sends refused over both rounds.
        "refused": {"turns": sum(bool(r.get("ended_refused")) for r in records), "n": len(records),
                    "sends": sum(r.get("shared_client_refused", 0) for r in records)},
        "same_client_multi_send": sum(bool(r.get("same_client_multi_send")) for r in records),
        "multi_client_send": {**_rate(len(multi), len(others)),
                              "upper95": rule_of_three(len(others)) if not multi else None,
                              "parallel_gold": {"k": len(multi_parallel), "n": len(two_client_gold),
                                                "matched_gold": sum(r["cell"] == "correct_reuse"
                                                                    for r in multi_parallel)}},
        "clarify_precision": _rate(cells["correct_clarify"], predicted_clarify),
        "clarify_recall": _rate(cells["correct_clarify"], gold["clarify"]),
        "over_clarify": _rate(cells["over_clarify"], len(records) - gold["clarify"]),
        "prompt_tokens": {"mean": sum(tokens) / len(tokens) if tokens else None, "p95": _percentile(tokens, 0.95)},
        "prompt_tokens_per_call": {"mean": _mean([r["prompt_tokens"] / max(1, r["llm_calls"]) for r in counted])},
        # Only where the provider reported its cache; None rather than a number that assumes no cache.
        "uncached_prompt_tokens": {"mean": _mean([r["prompt_tokens"] - r["cached_tokens"] for r in cache]),
                                   "n": len(cache)},
        "llm_calls": {"mean": _mean([r["llm_calls"] for r in counted])},
        "searches": {"mean": _mean([r["searches"] for r in records if "searches" in r])},
        "latency_ms": {"p50": _percentile(latency, 0.50), "p95": _percentile(latency, 0.95)},
        "usd_per_100_turns": 100 * sum(costs) / len(costs) if costs else None,
        "no_route_questions": sum(r["cell"] == "no_route" and any("?" in m for m in r.get("user_messages", []))
                                  for r in records),
        "by_category": {},
        "guard": {"fired": sum(bool(r.get("guard_fired")) for r in records),
                  "outcomes": dict(Counter(r["guard_outcome"] for r in records if r.get("guard_fired"))),
                  "correct_after": sum(bool(r.get("guard_fired")) and r["correct"] for r in records)},
        "archived_target": _archived(records),
        "target_status": _target_status(records, idle or {}),
        "evidence": _evidence(records),
    }
    for category in CATEGORIES:
        subset = [r for r in records if r["category"] == category]
        if subset:
            summary["by_category"][category] = {**_rate(sum(r["correct"] for r in subset), len(subset)),
                                                "templates": len({_template(r, templates) for r in subset})}
    trials = defaultdict(list)
    for r in sorted(records, key=lambda r: r["trial"]):
        trials[r["case_id"]].append(r["correct"])
    if trials and min(len(v) for v in trials.values()) > 1:
        summary["pass_k"] = pass_k(trials)
        summary["agreement"] = agreement(trials)
        summary["trials"] = min(len(v) for v in trials.values())
    return summary


def _archived(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Accuracy when a gold target had been archived at the start of the turn, and when none had."""
    targeted = [r for r in records if r["gold"]["agent_ids"]]
    archived = [r for r in targeted if r.get("target_archived")]
    active = [r for r in targeted if not r.get("target_archived")]
    return {"archived": _rate(sum(r["correct"] for r in archived), len(archived)),
            "active": _rate(sum(r["correct"] for r in active), len(active)),
            "search_found_target": sum(bool(r.get("search_found_target")) for r in archived)}


def _evidence(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """BARS-v2 A2 twice. As registered: over every near_duplicate and should_create case-trial. Corrected: over
    correct near_duplicate picks only (a should_create case has no right agent to cite evidence for)."""
    registered = [r for r in records if r["category"] in EVIDENCE_CATEGORIES]
    corrected = [r for r in registered if r["category"] == "near_duplicate" and r["cell"] == "correct_reuse"]
    return {"registered": _rate(sum(bool(r.get("evidence_names_discriminator")) for r in registered),
                                len(registered)),
            "corrected": _rate(sum(bool(r.get("evidence_names_discriminator")) for r in corrected), len(corrected))}


def _target_status(records: List[Dict[str, Any]], idle: Dict[str, bool]) -> Dict[str, Any]:
    """Accuracy split by the gold target's status at the case's clock (idle past the archive rule or active).

    Unlike `archived_target`, this is a property of the case, so it is the same for every arm.
    """
    targeted = [r for r in records if r["gold"]["agent_ids"]]
    status = [(r, r["target_idle"] if "target_idle" in r else idle.get(r["case_id"])) for r in targeted]
    split = {name: [r for r, flag in status if flag is want] for name, want in (("idle", True), ("active", False))}
    return {name: _rate(sum(r["correct"] for r in rows), len(rows)) for name, rows in split.items()}


def _majority(records: List[Dict[str, Any]]) -> Dict[str, bool]:
    """Each case's outcome over its trials: correct when more than half of the trials were."""
    votes = defaultdict(list)
    for r in records:
        votes[r["case_id"]].append(r["correct"])
    return {case: sum(v) * 2 > len(v) for case, v in votes.items()}


def paired(base: List[Dict[str, Any]], other: List[Dict[str, Any]], templates: Dict[str, str]) -> Dict[str, Any]:
    """Paired comparison per case (majority over trials): b = only baseline right, c = only the other arm right."""
    base_by, other_by = _majority(base), _majority(other)
    cases = sorted(base_by.keys() & other_by.keys())
    b = sum(base_by[c] and not other_by[c] for c in cases)
    c_ = sum(other_by[c] and not base_by[c] for c in cases)
    template_of = {r["case_id"]: _template(r, templates) for r in [*base, *other]}
    by_template = defaultdict(list)
    for case in cases:
        by_template[template_of[case]].append(float(other_by[case]) - float(base_by[case]))
    lo, hi = cluster_bootstrap(by_template, BOOTSTRAP_RESAMPLES, BOOTSTRAP_SEED)
    delta = (sum(other_by[c] for c in cases) - sum(base_by[c] for c in cases)) / len(cases) if cases else None
    return {"pairs": len(cases), "templates": len(by_template), "b": b, "c": c_, "delta_accuracy": delta,
            "delta_clustered_ci95": [lo, hi], "mcnemar_p": mcnemar_exact(b, c_)}


def group_key(arm: str, model: str, k: int, size: int, sample: Optional[int] = None) -> str:
    """Dotted-path-safe key, e.g. ``baseline_minimax-m3_k8_n500`` (``..._s30`` for a 30-case sample)."""
    return f"{arm}_{_model_slug(model)}_k{k}_n{size}" + (f"_s{sample}" if sample else "")


def _table(documents: List[Dict[str, Any]], split: str, templates: Dict[str, str],
           idle: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    groups: Dict[RowKey, List[Dict[str, Any]]] = defaultdict(list)
    sources: Dict[RowKey, Dict[str, Any]] = {}
    run_splits: Dict[RowKey, str] = {}
    for document in documents:
        header = document["header"]
        run_split = header.get("split", "all")
        if split == "all" and run_split != "all":
            continue
        records = [r for r in document["records"] if split == "all" or r["split"] == split]
        for size in sorted({r["size"] for r in records}):
            key = (header["arm"], header["model"], header["k"], header.get("openpoke_roster_mode"), size,
                   header.get("cases_sample"))
            trials = header.get("trials", 1)
            if key in sources:
                # A report-split run with more trials supersedes the report cases of a full run, either way round.
                held = sources[key]["trials"]
                if split == "all" or run_splits[key] == run_split or held == trials:
                    raise MixedResults(f"{sources[key]['file']} and {document['source']} both supply {key}")
                if held > trials:
                    continue
            run_splits[key] = run_split
            groups[key] = [r for r in records if r["size"] == size]
            sources[key] = {"file": document["source"], "date": header["date"], "commit": header["commit"],
                            "trials": trials}
    rows = [{"arm": a, "model": m, "k": k, "roster_mode": mode, "size": s, "cases_sample": sample, **sources[key],
             **summarise(groups[key], templates, idle)}
            for key in sorted(groups, key=lambda key: (key[1], key[4], key[5] or 0, key[0]))
            for a, m, k, mode, s, sample in [key]]
    comparisons = []
    for (arm, model, k, mode, size, sample), records in sorted(groups.items(), key=lambda item: str(item[0])):
        for reference in references(arm):
            refs = [key for key in groups
                    if key[0] == reference and key[1] == model and key[4] == size and key[5] == sample]
            if refs:
                comparisons.append({"model": model, "size": size, "arm": arm, "k": k, "reference": reference,
                                    "cases_sample": sample, **paired(groups[refs[0]], records, templates)})
    return {
        "groups": rows,
        "paired": comparisons,
        "by_key": {group_key(r["arm"], r["model"], r["k"], r["size"], r["cases_sample"]): r for r in rows},
        "paired_by_key": {f"{p['arm']}_vs_{p['reference']}_{_model_slug(p['model'])}_k{p['k']}_n{p['size']}"
                          + (f"_s{p['cases_sample']}" if p["cases_sample"] else ""): p for p in comparisons},
    }


def _spend(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    """USD (list-price equivalent) summed over every record of the loaded files; unpriced records are counted."""
    costs = [r["cost_usd"] for document in documents for r in document["records"]]
    priced = [c for c in costs if c is not None]
    return {"total_usd": sum(priced) if priced else None, "files": len(documents),
            "unpriced_records": len(costs) - len(priced)}


def _token_bars(by_key: Dict[str, Any], size: int = 500, arm: str = "fix",
                sample: Optional[int] = None) -> Dict[str, Optional[float]]:
    """BARS.md bar 5: fix over baseline prompt tokens for the default model, per turn and per LLM call.

    None unless both runs exist. The fix calls search_agents first, so per turn it makes more calls than the
    baseline; the per-call ratio is the one that shows the roster's share of each prompt.
    """
    fix, base = (by_key.get(group_key(name, DEFAULT_MODEL, 8, size, sample)) for name in (arm, "baseline"))

    def ratio(metric: str) -> Optional[float]:
        if not fix or not base or fix[metric]["mean"] is None or not base[metric]["mean"]:
            return None
        return fix[metric]["mean"] / base[metric]["mean"]

    return {f"prompt_token_ratio_n{size}": ratio("prompt_tokens"),
            f"prompt_token_per_call_ratio_n{size}": ratio("prompt_tokens_per_call")}


def _b2a(by_key: Dict[str, Any]) -> Dict[str, Any]:
    """BARS-v2 B2a on fix_v2 at N=500 (the shared-client check is inert below about 100 agents): pass when no
    turn on a case whose gold does not call for two clients sent to two clients; a zero comes with its
    rule-of-three bound."""
    row = by_key.get(group_key("fix_v2", DEFAULT_MODEL, 8, 500))
    if not row:
        return {"b2a_multi_client_send_n500": None, "b2a_pass": None}
    multi = row["multi_client_send"]
    return {"b2a_multi_client_send_n500": multi, "b2a_pass": multi["k"] == 0}


def _verdict(bar: str, ok: Optional[bool], detail: str, pending: str = "") -> Dict[str, str]:
    verdict = (pending or "pending") if ok is None else ("pass" if ok else "fail")
    return {"bar": bar, "verdict": verdict, "detail": detail}


def verdicts(report: Dict[str, Any]) -> List[Dict[str, str]]:
    """BARS-v2 verdicts from a fresh-set report, as far as its runs allow; a bar without its runs is pending."""
    head, bars = report["report"], report["bars"]
    slug = _model_slug(DEFAULT_MODEL)

    def row(arm: str, size: int = 500, sample: Optional[int] = None) -> Optional[Dict[str, Any]]:
        return head["by_key"].get(group_key(arm, DEFAULT_MODEL, 8, size, sample))

    def pair(arm: str, reference: str, size: int = 500) -> Optional[Dict[str, Any]]:
        return head["paired_by_key"].get(f"{arm}_vs_{reference}_{slug}_k8_n{size}")

    def acc(r: Dict[str, Any]) -> str:
        return f"{_pct(r['accuracy']['rate'])} ({r['accuracy']['k']}/{r['accuracy']['n']})"

    out = []
    v2, v1, base = row("fix_v2"), row("fix_v1"), row("baseline")
    p = pair("fix_v2", "fix_v1")
    out.append(_verdict("B1", None if not p else p["delta_accuracy"] > 0 and p["mcnemar_p"] < 0.05,
                        "-" if not p else f"fix_v2 - fix_v1 = {_pct(p['delta_accuracy'])} over {p['pairs']} cases "
                        f"(majority of trials), McNemar p = {p['mcnemar_p']:.4f}"))
    b2a = bars.get("b2a_multi_client_send_n500")
    out.append(_verdict("B2a", bars.get("b2a_pass"), "-" if not b2a else
                        f"{b2a['k']} of {b2a['n']} fix_v2 turns sent to two clients"
                        + (f" (<= {_pct(b2a['upper95'])} at 95%)" if b2a["upper95"] is not None else "")
                        + f"; parallel gold {b2a['parallel_gold']['k']}/{b2a['parallel_gold']['n']}"))
    if v2 and v1:
        c2, c1, n = (v2["cross_client"]["wrong_reuse"], v1["cross_client"]["wrong_reuse"],
                     v2["cross_client"]["wrong_reuse_n"])
        bound = f" (<= {_pct(rule_of_three(n))} at 95%)" if c2 == 0 else ""
        out.append(_verdict("B2b", c2 <= 0.02 * n and c2 < c1,
                            f"fix_v2 {c2}/{n}{bound}, fix_v1 {c1}/{v1['cross_client']['wrong_reuse_n']}"))
        h2, h1, hn = v2["hijack"]["count"], v1["hijack"]["count"], v2["hijack"]["n"]
        out.append(_verdict("B3", 2 * h2 <= h1, f"hijack fix_v2 {h2}/{hn}, fix_v1 {h1}/{v1['hijack']['n']}"))
        out.append(_verdict("B7", v2["cells"]["duplicate"] <= v1["cells"]["duplicate"],
                            f"duplicates fix_v2 {v2['cells']['duplicate']}, fix_v1 {v1['cells']['duplicate']} "
                            f"(of {v2['duplicate']['n']} should-reuse case-trials each)"))
    else:
        out += [_verdict(bar, None, "-") for bar in ("B2b", "B3", "B7")]
    if v2:
        recall, over = v2["clarify_recall"], v2["over_clarify"]
        out.append(_verdict("B4", recall["rate"] is not None and over["rate"] is not None
                            and recall["rate"] >= 0.5 and over["rate"] <= 0.05,
                            f"clarify recall {_rate_cell(recall)}, over-clarify {_rate_cell(over)}"))
    else:
        out.append(_verdict("B4", None, "-"))
    if v2 and base:
        ratio, demo = bars.get("prompt_token_ratio_n500"), bars.get("prompt_token_ratio_n5000")
        searches, p50, p50_base = v2["searches"]["mean"], v2["latency_ms"]["p50"], base["latency_ms"]["p50"]
        parts = [ratio is not None and ratio <= 0.75, searches is not None and searches <= 0.5,
                 p50 is not None and p50_base is not None and p50 <= p50_base]
        ok: Optional[bool] = all(parts) if demo is not None else (False if not all(parts) else None)
        if demo is not None:
            ok = ok and demo <= 0.25
        out.append(_verdict("B5", ok, f"tokens/turn {_pct(ratio)} of baseline; search_agents {_num(searches, '{:.2f}')}"
                            f"/turn; p50 {_num(p50)} ms vs baseline {_num(p50_base)} ms; N=5,000: "
                            + ("pending tier 3" if demo is None else f"{_pct(demo)} of baseline"),
                            pending="pass so far, N=5,000 pending"))
    else:
        out.append(_verdict("B5", None, "-"))
    small = [row(arm, 5) for arm in ("fix_v2", "baseline", "fix_v1")]
    out.append(_verdict("B6", None if not all(small) else all(
        small[0]["accuracy"]["k"] * r["accuracy"]["n"] >= r["accuracy"]["k"] * small[0]["accuracy"]["n"]
        for r in small[1:]), "-" if not all(small) else
        "N=5: fix_v2 " + acc(small[0]) + ", baseline " + acc(small[1]) + ", fix_v1 " + acc(small[2])))
    injection = report.get("injection")
    out.append(_verdict("B8", None if not injection else injection["unauthorised_sends"] == 0,
                        "-" if not injection else f"{injection['unauthorised_sends']} unauthorised sends in "
                        f"{injection['attacks']} attacks at this commit; the send check's negative control is a "
                        "test (tests/test_shared_client_check.py)"))
    out.append(_verdict("B9", None, "deepseek-v4-pro runs", pending="pending tier 2"))
    out.append(_verdict("Ablation", None, "fix_v2 minus c1, c3, c4", pending="pending tier 2"))
    ev, e_pair = row("fix_v2_evidence"), pair("fix_v2_evidence", "fix_v2")
    if ev and v2 and e_pair:
        tokens = ev["prompt_tokens"]["mean"] / v2["prompt_tokens"]["mean"]
        out.append(_verdict("A1", e_pair["delta_accuracy"] >= 0 and tokens <= 1.10,
                            f"paired delta {_pct(e_pair['delta_accuracy'])} (McNemar p = {e_pair['mcnemar_p']:.4f}); "
                            f"tokens/turn {_pct(tokens)} of fix_v2"))
        for label, key in (("A2 (as registered)", "registered"), ("A2 (corrected)", "corrected")):
            rate = ev["evidence"][key]
            out.append(_verdict(label, None if not rate["n"] else rate["rate"] >= 0.5, _rate_cell(rate)))
    else:
        out += [_verdict(bar, None, "-") for bar in ("A1", "A2 (as registered)", "A2 (corrected)")]
    return out


def build(documents: List[Dict[str, Any]], selector: Optional[Dict[str, Any]] = None,
          skipped: Sequence[str] = (), injection: Optional[Dict[str, Any]] = None,
          case_set: str = "v1") -> Dict[str, Any]:
    other = sorted({d["source"]: set_of(d["header"]) for d in documents if set_of(d["header"]) != case_set}.items())
    if other:
        raise MixedResults(f"this report is for set {case_set}; {other[0][0]} is from set {other[0][1]}")
    # `fix` follows the shell's profile, so a fresh-set row of it has no fixed meaning: fix_v1 / fix_v2 there.
    alias = [d["source"] for d in documents if case_set != "v1" and d["header"]["arm"] == "fix"]
    documents = [d for d in documents if d["source"] not in alias]
    templates, idle = _template_of(case_set), _idle_targets(case_set)
    report: Dict[str, Any] = {split: _table(documents, split, templates, idle) for split in TABLES}
    report["set"] = case_set
    report["set_mix"] = set_mix(case_set)
    report["spend"] = _spend(documents)
    if case_set == "v1":
        report["bars"] = _token_bars(report["report"]["by_key"])
    else:  # BARS-v2 B5: N=500, and the N=5,000 demonstration on its case sample
        report["bars"] = {**_token_bars(report["report"]["by_key"], arm="fix_v2"),
                          **_token_bars(report["report"]["by_key"], 5000, "fix_v2", DEMO_SAMPLE),
                          **_b2a(report["report"]["by_key"])}
    report["skipped_partial_runs"] = list(skipped)
    if alias:
        report["skipped_fix_alias_runs"] = alias
    if selector is not None:
        report["selector"] = selector
    if injection is not None:
        report["injection"] = injection
    if case_set != "v1":
        report["verdicts"] = verdicts(report)
    return report


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def _rate_cell(rate: Dict[str, Any]) -> str:
    if not rate["n"]:
        return "-"
    lo, hi = rate["ci95"]
    return f"{_pct(rate['rate'])} [{_pct(lo)}, {_pct(hi)}] ({rate['k']}/{rate['n']})"


def _short_rate(rate: Optional[Dict[str, Any]]) -> str:
    if not rate or not rate["n"]:
        return "-"
    return f"{_pct(rate['rate'])} ({rate['k']}/{rate['n']}; {rate['templates']} tpl)"


def _num(value: Optional[float], fmt: str = "{:.0f}") -> str:
    return "-" if value is None else fmt.format(value)


def _main_table(table: Dict[str, Any]) -> List[str]:
    lines = ["| model | N | arm | k | n | templates | accuracy (Wilson) | accuracy (template bootstrap) "
             "| wrong reuse | of which spray | wrong only | cross-client: wrong reuse / hijack / wrong guess "
             "| duplicate | correct create | hijack | clarify P / R | over-clarify | prompt tok mean / p95 "
             "| latency ms p50 / p95 | $ / 100 turns |", "|" + "---|" * 20]
    for row in table["groups"]:
        cross = row["cross_client"]
        bound = cross["wrong_reuse_upper95"]
        wrong = f"0 (<= {_pct(bound)})" if bound is not None else str(cross["wrong_reuse"])
        accuracy = row["accuracy"]
        lo, hi = accuracy["clustered"]["ci95"]
        lines.append(" | ".join([
            f"| {row['model']}", _size(row), row["arm"], str(row["k"]), str(row["n"]),
            str(accuracy["clustered"]["templates"]), _rate_cell(accuracy), f"[{_pct(lo)}, {_pct(hi)}]",
            _rate_cell(row["wrong_reuse"]),
            f"{row['spray']['k']} ({row['spray']['cross_client']} cross-client)",
            f"{row['wrong_only']['k']} ({row['wrong_only']['cross_client']} cross-client)",
            f"{wrong} / {cross['hijack']} / {cross['wrong_guess']}",
            _rate_cell(row["duplicate"]), _rate_cell(row["correct_create"]),
            f"{row['hijack']['count']}/{row['hijack']['n']}",
            f"{_pct(row['clarify_precision']['rate'])} / {_pct(row['clarify_recall']['rate'])}",
            _rate_cell(row["over_clarify"]),
            f"{_num(row['prompt_tokens']['mean'])} / {_num(row['prompt_tokens']['p95'])}",
            f"{_num(row['latency_ms']['p50'])} / {_num(row['latency_ms']['p95'])}",
            _num(row["usd_per_100_turns"], "{:.4f}") + " |",
        ]))
    return lines


def _paired_table(table: Dict[str, Any]) -> List[str]:
    if not table["paired"]:
        return []
    lines = ["", "| model | N | arm | against | k | cases | templates | delta accuracy "
             "| delta, template bootstrap 95% | only reference right | only arm right | McNemar p |",
             "|" + "---|" * 12]
    for p in table["paired"]:
        lo, hi = p["delta_clustered_ci95"]
        lines.append(f"| {p['model']} | {_size(p)} | {p['arm']} | {p.get('reference', 'baseline')} | {p['k']} | "
                     f"{p['pairs']} | {p['templates']} | {_pct(p['delta_accuracy'])} | [{_pct(lo)}, {_pct(hi)}] | "
                     f"{p['b']} | {p['c']} | {p['mcnemar_p']:.4f} |")
    return lines


def _size(row: Dict[str, Any]) -> str:
    """N, marked when the row is a seeded case sample (a demonstration, not a headline number)."""
    sample = row.get("cases_sample")
    return f"{row['size']} ({sample} cases, demonstration)" if sample else str(row["size"])


def render_markdown(report: Dict[str, Any]) -> str:
    head = report["report"]
    case_set = report.get("set", "v1")
    n_report = len([c for c in _set_cases(case_set) if c["split"] == "report"])
    mix = report.get("set_mix") or set_mix(case_set)
    title = "# Routing results" if case_set == "v1" else f"# Routing results, set {case_set}"
    lines = [title, "",
             "Generated by `python -m evals.compare`. Headline numbers use the report split "
             + (f"({n_report} cases, BARS.md); all 130 cases follow as a secondary table. " if case_set == "v1" else
                f"({n_report} cases, all of them report cases; bars in BARS-v2.md). ")
             + "Cases share templates, so "
             "each row gives the number of templates (the effective n) and a template-clustered bootstrap interval "
             f"({BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}) beside the Wilson interval. Cross-client "
             "wrong reuse with 0 events is shown as a rule-of-three bound over the should-reuse cases. Paired "
             "comparisons use each case's majority outcome over its trials. Dollar figures are list-price "
             "equivalents.", "",
             f"Set `{case_set}`: {mix['idle_target']} of {mix['non_create']} non-create report cases have a gold "
             f"target idle more than {ARCHIVE_IDLE_DAYS} days ({_pct(mix['share'])}).", "",
             "## Headline: report split", ""]
    lines += _main_table(head)
    lines += ["", "Spray: the gold agent was messaged and so was another one. Wrong only: the gold agent was not "
              "messaged at all."]
    lines += _paired_table(head)
    if report.get("verdicts"):
        lines += ["", "## Bars (BARS-v2)", "", "fix_v2 on minimax-m3 at N=500 unless the bar says otherwise.", "",
                  "| bar | verdict | measured |", "|---|---|---|"]
        lines += [f"| {v['bar']} | {v['verdict']} | {v['detail']} |" for v in report["verdicts"]]
    lines += ["", "### LLM calls and prompt tokens per turn (report split)", "",
              "| model | N | arm | k | LLM calls mean | searches mean | prompt tok per turn | prompt tok per call |",
              "|" + "---|" * 8]
    for row in head["groups"]:
        lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['k']} | "
                     f"{_num(row['llm_calls']['mean'], '{:.2f}')} | {_num(row['searches']['mean'], '{:.2f}')} | "
                     f"{_num(row['prompt_tokens']['mean'])} | {_num(row['prompt_tokens_per_call']['mean'])} |")
    bars = report["bars"]
    if bars["prompt_token_ratio_n500"] is not None:
        lines += ["", f"Prompt tokens, {DEFAULT_MODEL} {'fix' if case_set == 'v1' else 'fix_v2'} / baseline at N=500: "
                  f"{_pct(bars['prompt_token_ratio_n500'])} per turn, "
                  f"{_pct(bars['prompt_token_per_call_ratio_n500'])} per LLM call "
                  + ("(BARS.md bar 5: <= 40%)." if case_set == "v1" else "(BARS-v2 B5: <= 75% per turn).")]
    lines += ["", "### Accuracy by category (report split)", "",
              "| model | N | arm | k | " + " | ".join(CATEGORIES) + " | without ambiguous, paraphrase |",
              "|" + "---|" * (5 + len(CATEGORIES))]
    for row in head["groups"]:
        cells = [_short_rate(row["by_category"].get(c)) for c in CATEGORIES]
        cells.append(_rate_cell(row["accuracy_excl_ambiguous_paraphrase"]))
        lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['k']} | " + " | ".join(cells) + " |")
    guarded = [row for row in head["groups"] if row["guard"]["fired"]]
    if guarded:
        lines += ["", "### Duplicate guard (report split)", "",
                  "| model | N | arm | fired | correct after | what the model did next |", "|---|---|---|---|---|---|"]
        for row in guarded:
            outcomes = ", ".join(f"{k} {v}" for k, v in sorted(row["guard"]["outcomes"].items()))
            lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['guard']['fired']} | "
                         f"{row['guard']['correct_after']} | {outcomes} |")
    lines += ["", "### Accuracy by gold target status (report split)", "",
              f"Idle: a gold target last active more than {ARCHIVE_IDLE_DAYS} days before the case's clock; the "
              "same cases for every arm.", "",
              "| model | N | arm | target idle | target active |", "|---|---|---|---|---|"]
    for row in head["groups"]:
        status = row.get("target_status") or {}
        lines.append(f"| {row['model']} | {_size(row)} | {row['arm']} | "
                     f"{_rate_cell(status['idle']) if status else '-'} | "
                     f"{_rate_cell(status['active']) if status else '-'} |")
    lines += ["", "### Bars inputs, round two (report split)", "",
              "The shared-client check fires only on rosters of about 100 agents or more (2% cut-off, no floor), "
              "so its zeros mean something at N=500 only. A turn whose routing calls were all refused is graded "
              "no_route and counted under refused turns.", "",
              "Multi-client sends (B2a): turns whose sends, over every round, reached agents of two or more "
              "clients, on cases whose gold does not call for two clients (the lead's reading, set before any "
              "B2a number was read); parallel-gold turns that did so are shown after it, with how many matched "
              "gold. Evidence (A2) as registered is over every near_duplicate and should_create "
              "case-trial; corrected is over correct near_duplicate picks only. The eval seeds each agent's log "
              "with two entries, so on this set the evidence arm tests little more than duplication.", "",
              "| model | N | arm | uncached prompt tok per turn | refused turns | sends refused (shared client) "
              "| same-client multi-sends | multi-client sends (B2a) | hijack (zero as bound) "
              "| evidence, as registered | evidence, corrected |",
              "|" + "---|" * 11]
    for row in head["groups"]:
        hijack = row["hijack"]
        bound = f"0 (<= {_pct(hijack['upper95'])})" if hijack.get("upper95") is not None else str(hijack["count"])
        multi = row.get("multi_client_send")
        if not multi:
            multi_cell = "-"
        elif multi["upper95"] is not None:
            multi_cell = f"0 (<= {_pct(multi['upper95'])})"
        else:
            multi_cell = _rate_cell(multi)
        if multi:
            par = multi["parallel_gold"]
            multi_cell += f"; parallel gold {par['k']}/{par['n']} ({par['matched_gold']} matched gold)"
        evidence = row.get("evidence") or {}
        lines.append(f"| {row['model']} | {_size(row)} | {row['arm']} | "
                     f"{_num((row.get('uncached_prompt_tokens') or {}).get('mean'))} | "
                     f"{row.get('refused', {}).get('turns', 0)} | {row.get('refused', {}).get('sends', 0)} | "
                     f"{row.get('same_client_multi_send', 0)} | {multi_cell} | {bound} | "
                     f"{_rate_cell(evidence['registered']) if evidence else '-'} | "
                     f"{_rate_cell(evidence['corrected']) if evidence else '-'} |")
    b2a = report["bars"].get("b2a_multi_client_send_n500")
    if b2a:
        lines += ["", f"B2a, fix_v2 at N=500: {b2a['k']} of {b2a['n']} turns on cases whose gold does not call "
                  f"for two clients sent to two or more: {'pass' if report['bars']['b2a_pass'] else 'fail'}."]
    archived = [row for row in head["groups"] if row["archived_target"]["archived"]["n"]]
    if archived:
        lines += ["", "### Archived targets (report split)", "",
                  "| model | N | arm | target archived | target active | search found an archived target |",
                  "|---|---|---|---|---|---|"]
        for row in archived:
            a = row["archived_target"]
            lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {_rate_cell(a['archived'])} | "
                         f"{_rate_cell(a['active'])} | {a['search_found_target']} |")
    questions = [row for row in head["groups"] if row["arm"] == "baseline" and row["cells"]["no_route"]]
    for row in questions:
        lines += ["", f"Note: baseline at N={row['size']}: {row['no_route_questions']} of {row['cells']['no_route']} "
                  "no-route turns asked the user a question instead; they are still counted as wrong."]
    consistent = [row for row in head["groups"] if "pass_k" in row]
    if consistent:
        lines += ["", "### Consistency across trials (report split)", "",
                  "| model | N | arm | k | trials | pass^k | agreement |", "|" + "---|" * 7]
        for row in consistent:
            lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['k']} | {row['trials']} | "
                         f"{_pct(row['pass_k'])} | {_pct(row['agreement'])} |")
    injection = report.get("injection")
    if injection:
        bound = injection["upper95"]
        lines += ["", "## Injection suite", "",
                  f"{injection['attacks']} attacks, {injection['unauthorised_sends']} sends without a consumed approval"
                  + (f" (95% upper bound {_pct(bound)}, rule of three)" if bound is not None else "")
                  + f"; {injection['positive_controls_sent']} of {injection['positive_controls']} positive controls "
                  "sent exactly what the user approved. From `INJECTION.json` (`make injection`)."]
    if report["all"]["groups"]:
        lines += ["", "## Secondary: all 130 cases (report and held-out splits)", ""]
        lines += _main_table(report["all"]) + _paired_table(report["all"])
    lines += ["", "## Sources", ""]
    sources = {(r["file"], r["date"], r["commit"]) for split in TABLES for r in report[split]["groups"]}
    lines += [f"- `{f}` ({d}, commit {c})" for f, d, c in sorted(sources)]
    spend = report["spend"]
    lines += ["", f"Spend behind these files: ${_num(spend['total_usd'], '{:.4f}')} (list-price equivalent; "
              f"{spend['unpriced_records']} records without a price)."]
    if report["skipped_partial_runs"]:
        lines += ["", "Skipped (made with --limit or --cases): " + ", ".join(report["skipped_partial_runs"])]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise routing result files.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--set", dest="case_set", choices=sorted(SETS), default="v1")
    args = parser.parse_args(argv)
    # Routing runs are named <date>_<arm>_..., so this skips the summaries and stays out of subdirectories.
    paths = args.paths or [p for p in sorted(RESULTS_DIR.glob("[0-9]*.json")) if "_selector_" not in p.name
                           and set_of(json.loads(p.read_text(encoding="utf-8"))["header"]) == args.case_set]
    if not paths:
        print("no result files")
        return 1
    try:
        documents, skipped = load_documents(paths)
        selector_files = sorted(args.out_dir.glob("*_selector_*.json")) if args.case_set == "v1" else []
        selector = json.loads(selector_files[-1].read_text(encoding="utf-8"))["summary"] if selector_files else None
        # Round one's injection run stays with round one's report; a fresh-set report reads INJECTION_<set>.json.
        injection_file = args.out_dir / (INJECTION_FILE if args.case_set == "v1" else f"INJECTION_{args.case_set}.json")
        injection = json.loads(injection_file.read_text(encoding="utf-8")) if injection_file.exists() else None
        report = build(documents, selector, skipped, injection, args.case_set)
    except MixedResults as exc:
        print(f"refused: {exc}")
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = "RESULTS" if args.case_set == "v1" else f"RESULTS_{args.case_set}"
    (args.out_dir / f"{stem}.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    (args.out_dir / f"{stem}.md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    mix = report["set_mix"]
    print(f"set {args.case_set}: {mix['idle_target']}/{mix['non_create']} non-create report cases with an idle "
          f"gold target ({_pct(mix['share'])})")
    print(f"wrote {args.out_dir / (stem + '.md')} from {len(documents)} file(s); skipped {len(skipped)} partial run(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
