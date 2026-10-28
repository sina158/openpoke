"""Summarise routing result files into evals/results/RESULTS.md and RESULTS.json.

    python -m evals.compare                    # every full result file in evals/results
    python -m evals.compare a.json b.json      # just these

The headline tables use the report split (BARS.md); all 130 cases are a secondary table. Runs made with
--limit or --cases are skipped, and two files with the same configuration are refused rather than one silently
replacing the other. Rates use the denominator that matches the question: wrong reuse and duplicates over cases
whose gold is an existing agent, correct create and hijacks over should-create cases, clarify recall over
ambiguous cases. Cases built from one template are not independent, so every group also reports its number of
templates (the effective n) and a template-clustered bootstrap interval.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evals.generate import load_cases
from evals.run_routing import CELLS, RESULTS_DIR, _model_slug
from evals.stats import agreement, cluster_bootstrap, mcnemar_exact, pass_k, rule_of_three, wilson

CATEGORIES = ("explicit", "entity", "paraphrase", "pronoun", "near_duplicate", "should_create", "ambiguous",
              "parallel", "agent_message")
TABLES = ("report", "all")
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261028
RowKey = Tuple[str, str, int, str, int]  # arm, model, k, roster mode, size


class MixedResults(ValueError):
    """Two result files would feed the same row."""


def config_key(header: Dict[str, Any]) -> tuple:
    return (header["arm"], header["model"], header["k"], header.get("split", "all"), tuple(header["sizes"]),
            header.get("trials", 1), header.get("openpoke_roster_mode"))


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


def _template_of() -> Dict[str, str]:
    return {case["case_id"]: case["template_id"] for case in load_cases()}


def _template(record: Dict[str, Any], templates: Dict[str, str]) -> str:
    return record.get("template_id") or templates.get(record["case_id"], record["case_id"])


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))
    return ordered[index]


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


def summarise(records: List[Dict[str, Any]], templates: Dict[str, str]) -> Dict[str, Any]:
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
    summary = {
        "n": len(records),
        "cells": cells,
        "accuracy": {**_rate(sum(r["correct"] for r in records), len(records)),
                     "clustered": _clustered(records, templates)},
        "wrong_reuse": _rate(cells["wrong_reuse"], n_existing),
        "spray": {**_rate(len(spray), n_existing), "cross_client": sum(r["cross_client"] for r in spray)},
        "wrong_only": {**_rate(len(wrong_only), n_existing),
                       "cross_client": sum(r["cross_client"] for r in wrong_only)},
        "cross_client": {**cross, "wrong_reuse_n": n_existing,
                         "wrong_reuse_upper95": rule_of_three(n_existing) if cross["wrong_reuse"] == 0 else None},
        "duplicate": _rate(cells["duplicate"], n_existing),
        "correct_create": _rate(cells["correct_create"], gold["create"]),
        "hijack": {"count": cells["hijack"], "n": gold["create"]},
        "clarify_precision": _rate(cells["correct_clarify"], predicted_clarify),
        "clarify_recall": _rate(cells["correct_clarify"], gold["clarify"]),
        "over_clarify": _rate(cells["over_clarify"], len(records) - gold["clarify"]),
        "prompt_tokens": {"mean": sum(tokens) / len(tokens) if tokens else None, "p95": _percentile(tokens, 0.95)},
        "latency_ms": {"p50": _percentile(latency, 0.50), "p95": _percentile(latency, 0.95)},
        "usd_per_100_turns": 100 * sum(costs) / len(costs) if costs else None,
        "no_route_questions": sum(r["cell"] == "no_route" and any("?" in m for m in r.get("user_messages", []))
                                  for r in records),
        "by_category": {},
        "guard": {"fired": sum(bool(r.get("guard_fired")) for r in records),
                  "outcomes": dict(Counter(r["guard_outcome"] for r in records if r.get("guard_fired"))),
                  "correct_after": sum(bool(r.get("guard_fired")) and r["correct"] for r in records)},
        "archived_target": _archived(records),
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


def group_key(arm: str, model: str, k: int, size: int) -> str:
    """Dotted-path-safe key, e.g. ``baseline_minimax-m3_k8_n500``."""
    return f"{arm}_{_model_slug(model)}_k{k}_n{size}"


def _table(documents: List[Dict[str, Any]], split: str, templates: Dict[str, str]) -> Dict[str, Any]:
    groups: Dict[RowKey, List[Dict[str, Any]]] = defaultdict(list)
    sources: Dict[RowKey, Dict[str, Any]] = {}
    for document in documents:
        header = document["header"]
        run_split = header.get("split", "all")
        if split == "all" and run_split != "all":
            continue
        records = [r for r in document["records"] if split == "all" or r["split"] == split]
        for size in sorted({r["size"] for r in records}):
            key = (header["arm"], header["model"], header["k"], header.get("openpoke_roster_mode"), size)
            if key in sources:
                raise MixedResults(f"{sources[key]['file']} and {document['source']} both supply {key}")
            groups[key] = [r for r in records if r["size"] == size]
            sources[key] = {"file": document["source"], "date": header["date"], "commit": header["commit"],
                            "trials": header.get("trials", 1)}
    rows = [{"arm": a, "model": m, "k": k, "roster_mode": mode, "size": s, **sources[key],
             **summarise(groups[key], templates)}
            for key in sorted(groups, key=lambda key: (key[1], key[4], key[0])) for a, m, k, mode, s in [key]]
    comparisons = []
    for (arm, model, k, mode, size), records in sorted(groups.items(), key=lambda item: str(item[0])):
        if arm == "baseline":
            continue
        baselines = [key for key in groups if key[0] == "baseline" and key[1] == model and key[4] == size]
        if baselines:
            comparisons.append({"model": model, "size": size, "arm": arm, "k": k,
                                **paired(groups[baselines[0]], records, templates)})
    return {
        "groups": rows,
        "paired": comparisons,
        "by_key": {group_key(r["arm"], r["model"], r["k"], r["size"]): r for r in rows},
        "paired_by_key": {f"{p['arm']}_vs_baseline_{_model_slug(p['model'])}_k{p['k']}_n{p['size']}": p
                          for p in comparisons},
    }


def build(documents: List[Dict[str, Any]], selector: Optional[Dict[str, Any]] = None,
          skipped: Sequence[str] = ()) -> Dict[str, Any]:
    templates = _template_of()
    report: Dict[str, Any] = {split: _table(documents, split, templates) for split in TABLES}
    report["skipped_partial_runs"] = list(skipped)
    if selector is not None:
        report["selector"] = selector
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
            f"| {row['model']}", str(row["size"]), row["arm"], str(row["k"]), str(row["n"]),
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
    lines = ["", "| model | N | arm | k | cases | templates | delta accuracy | delta, template bootstrap 95% "
             "| only baseline right | only arm right | McNemar p |", "|" + "---|" * 11]
    for p in table["paired"]:
        lo, hi = p["delta_clustered_ci95"]
        lines.append(f"| {p['model']} | {p['size']} | {p['arm']} | {p['k']} | {p['pairs']} | {p['templates']} | "
                     f"{_pct(p['delta_accuracy'])} | [{_pct(lo)}, {_pct(hi)}] | {p['b']} | {p['c']} | "
                     f"{p['mcnemar_p']:.4f} |")
    return lines


def render_markdown(report: Dict[str, Any]) -> str:
    head = report["report"]
    n_report = len([c for c in load_cases(split="report")])
    lines = ["# Routing results", "",
             "Generated by `python -m evals.compare`. Headline numbers use the report split "
             f"({n_report} cases, BARS.md); all 130 cases follow as a secondary table. Cases share templates, so "
             "each row gives the number of templates (the effective n) and a template-clustered bootstrap interval "
             f"({BOOTSTRAP_RESAMPLES} resamples, seed {BOOTSTRAP_SEED}) beside the Wilson interval. Cross-client "
             "wrong reuse with 0 events is shown as a rule-of-three bound over the should-reuse cases. Paired "
             "comparisons use each case's majority outcome over its trials. Dollar figures are list-price "
             "equivalents.", "", "## Headline: report split", ""]
    lines += _main_table(head)
    lines += ["", "Spray: the gold agent was messaged and so was another one. Wrong only: the gold agent was not "
              "messaged at all."]
    lines += _paired_table(head)
    lines += ["", "### Accuracy by category (report split)", "",
              "| model | N | arm | k | " + " | ".join(CATEGORIES) + " |", "|" + "---|" * (4 + len(CATEGORIES))]
    for row in head["groups"]:
        cells = [_short_rate(row["by_category"].get(c)) for c in CATEGORIES]
        lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['k']} | " + " | ".join(cells) + " |")
    guarded = [row for row in head["groups"] if row["guard"]["fired"]]
    if guarded:
        lines += ["", "### Duplicate guard (report split)", "",
                  "| model | N | arm | fired | correct after | what the model did next |", "|---|---|---|---|---|---|"]
        for row in guarded:
            outcomes = ", ".join(f"{k} {v}" for k, v in sorted(row["guard"]["outcomes"].items()))
            lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['guard']['fired']} | "
                         f"{row['guard']['correct_after']} | {outcomes} |")
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
    if report["all"]["groups"]:
        lines += ["", "## Secondary: all 130 cases (report and held-out splits)", ""]
        lines += _main_table(report["all"]) + _paired_table(report["all"])
    lines += ["", "## Sources", ""]
    sources = {(r["file"], r["date"], r["commit"]) for split in TABLES for r in report[split]["groups"]}
    lines += [f"- `{f}` ({d}, commit {c})" for f, d, c in sorted(sources)]
    if report["skipped_partial_runs"]:
        lines += ["", "Skipped (made with --limit or --cases): " + ", ".join(report["skipped_partial_runs"])]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise routing result files.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    paths = args.paths or sorted(p for p in RESULTS_DIR.glob("*.json")
                                 if p.name != "RESULTS.json" and "_selector_" not in p.name)
    if not paths:
        print("no result files")
        return 1
    try:
        documents, skipped = load_documents(paths)
        selector_files = sorted(args.out_dir.glob("*_selector_*.json"))
        selector = json.loads(selector_files[-1].read_text(encoding="utf-8"))["summary"] if selector_files else None
        report = build(documents, selector, skipped)
    except MixedResults as exc:
        print(f"refused: {exc}")
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "RESULTS.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    (args.out_dir / "RESULTS.md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    print(f"wrote {args.out_dir / 'RESULTS.md'} from {len(documents)} file(s); skipped {len(skipped)} partial run(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
