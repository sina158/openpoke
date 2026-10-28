"""Summarise routing result files into evals/results/RESULTS.md and RESULTS.json.

    python -m evals.compare                    # every result file in evals/results
    python -m evals.compare a.json b.json      # just these

When several files share (arm, model, k), the newest one is used. Rates use the denominator that matches the
question: wrong reuse and duplicates over cases whose gold is an existing agent, correct create and hijacks over
should-create cases, clarify recall over ambiguous cases.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from evals.run_routing import CELLS, RESULTS_DIR
from evals.stats import agreement, mcnemar_exact, pass_k, rule_of_three, wilson

GroupKey = Tuple[str, str, int, int]  # arm, model, k, size


def load_documents(paths: Sequence[Path]) -> List[Dict[str, Any]]:
    """Load result files, keeping the newest per (arm, model, k)."""
    latest: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for path in paths:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        document["source"] = Path(path).name
        header = document["header"]
        key = (header["arm"], header["model"], header["k"])
        if key not in latest or header["date"] >= latest[key]["header"]["date"]:
            latest[key] = document
    return list(latest.values())


def _percentile(values: List[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))
    return ordered[index]


def _rate(k: int, n: int) -> Dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": [lo, hi]}


def summarise(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """All the per-group numbers the report needs."""
    cells = {cell: sum(r["cell"] == cell for r in records) for cell in CELLS}
    gold = defaultdict(int)
    for r in records:
        gold[r["gold"]["kind"]] += 1
    n_existing = gold["reuse"] + gold["parallel"]
    predicted_clarify = sum(any(x["action"] == "clarify" for x in r["resolved"]) for r in records)
    cross = sum(r["cross_client"] for r in records)
    tokens = [r["prompt_tokens"] for r in records]
    latency = [r["latency_ms"] for r in records]
    costs = [r["cost_usd"] for r in records if r["cost_usd"] is not None]
    summary = {
        "n": len(records),
        "cells": cells,
        "accuracy": _rate(sum(r["correct"] for r in records), len(records)),
        "wrong_reuse": _rate(cells["wrong_reuse"], n_existing),
        "cross_client": {"count": cross, "n": len(records),
                         "upper95": rule_of_three(len(records)) if cross == 0 else None},
        "duplicate": _rate(cells["duplicate"], n_existing),
        "correct_create": _rate(cells["correct_create"], gold["create"]),
        "hijack": {"count": cells["hijack"], "n": gold["create"]},
        "clarify_precision": _rate(cells["correct_clarify"], predicted_clarify),
        "clarify_recall": _rate(cells["correct_clarify"], gold["clarify"]),
        "over_clarify": _rate(cells["over_clarify"], len(records) - gold["clarify"]),
        "prompt_tokens": {"mean": sum(tokens) / len(tokens) if tokens else None, "p95": _percentile(tokens, 0.95)},
        "latency_ms": {"p50": _percentile(latency, 0.50), "p95": _percentile(latency, 0.95)},
        "usd_per_100_turns": 100 * sum(costs) / len(costs) if costs else None,
    }
    trials = defaultdict(list)
    for r in sorted(records, key=lambda r: r["trial"]):
        trials[r["case_id"]].append(r["correct"])
    if trials and min(len(v) for v in trials.values()) > 1:
        summary["pass_k"] = pass_k(trials)
        summary["agreement"] = agreement(trials)
        summary["trials"] = min(len(v) for v in trials.values())
    return summary


def paired(base: List[Dict[str, Any]], other: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Paired comparison on (case, trial): b = only baseline right, c = only the other arm right."""
    base_by = {(r["case_id"], r["trial"]): r["correct"] for r in base}
    other_by = {(r["case_id"], r["trial"]): r["correct"] for r in other}
    keys = sorted(base_by.keys() & other_by.keys())
    b = sum(base_by[k] and not other_by[k] for k in keys)
    c = sum(other_by[k] and not base_by[k] for k in keys)
    delta = (sum(other_by[k] for k in keys) - sum(base_by[k] for k in keys)) / len(keys) if keys else None
    return {"pairs": len(keys), "b": b, "c": c, "delta_accuracy": delta, "mcnemar_p": mcnemar_exact(b, c)}


def build(documents: List[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[GroupKey, List[Dict[str, Any]]] = defaultdict(list)
    sources: Dict[GroupKey, Dict[str, Any]] = {}
    for document in documents:
        header = document["header"]
        for record in document["records"]:
            key = (header["arm"], header["model"], header["k"], record["size"])
            groups[key].append(record)
            sources[key] = {"file": document["source"], "date": header["date"], "commit": header["commit"]}
    rows = [{"arm": a, "model": m, "k": k, "size": s, **sources[(a, m, k, s)], **summarise(recs)}
            for (a, m, k, s), recs in sorted(groups.items(), key=lambda item: (item[0][1], item[0][3], item[0][0]))]
    comparisons = []
    for (arm, model, k, size), records in sorted(groups.items()):
        if arm == "baseline":
            continue
        baselines = [key for key in groups if key[0] == "baseline" and key[1] == model and key[3] == size]
        if baselines:
            comparisons.append({"model": model, "size": size, "arm": arm, "k": k,
                                **paired(groups[baselines[0]], records)})
    return {"groups": rows, "paired": comparisons}


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else f"{100 * value:.1f}%"


def _rate_cell(rate: Dict[str, Any]) -> str:
    if not rate["n"]:
        return "-"
    lo, hi = rate["ci95"]
    return f"{_pct(rate['rate'])} [{_pct(lo)}, {_pct(hi)}] ({rate['k']}/{rate['n']})"


def _num(value: Optional[float], fmt: str = "{:.0f}") -> str:
    return "-" if value is None else fmt.format(value)


def render_markdown(report: Dict[str, Any]) -> str:
    lines = ["# Routing results", "",
             "Generated by `python -m evals.compare` from the files listed per row. 95% Wilson intervals; "
             "cross-client wrong reuse with 0 events is shown as a rule-of-three upper bound. "
             "Dollar figures are list-price equivalents.", "",
             "| model | N | arm | k | n | accuracy | wrong reuse | cross-client | duplicate | correct create | hijack "
             "| clarify P / R | over-clarify | prompt tok mean / p95 | latency ms p50 / p95 | $ / 100 turns |",
             "|" + "---|" * 16]
    for row in report["groups"]:
        cross = row["cross_client"]
        cross_text = f"0 (<= {_pct(cross['upper95'])})" if cross["upper95"] is not None else str(cross["count"])
        lines.append(" | ".join([
            f"| {row['model']}", str(row["size"]), row["arm"], str(row["k"]), str(row["n"]),
            _rate_cell(row["accuracy"]), _rate_cell(row["wrong_reuse"]), cross_text, _rate_cell(row["duplicate"]),
            _rate_cell(row["correct_create"]), f"{row['hijack']['count']}/{row['hijack']['n']}",
            f"{_pct(row['clarify_precision']['rate'])} / {_pct(row['clarify_recall']['rate'])}",
            _rate_cell(row["over_clarify"]),
            f"{_num(row['prompt_tokens']['mean'])} / {_num(row['prompt_tokens']['p95'])}",
            f"{_num(row['latency_ms']['p50'])} / {_num(row['latency_ms']['p95'])}",
            _num(row["usd_per_100_turns"], "{:.4f}") + " |",
        ]))
    if report["paired"]:
        lines += ["", "## Paired against baseline (same cases and trials)", "",
                  "| model | N | arm | k | pairs | delta accuracy | only baseline right | only arm right | McNemar p |",
                  "|" + "---|" * 9]
        for p in report["paired"]:
            lines.append(f"| {p['model']} | {p['size']} | {p['arm']} | {p['k']} | {p['pairs']} | "
                         f"{_pct(p['delta_accuracy'])} | {p['b']} | {p['c']} | {p['mcnemar_p']:.4f} |")
    consistent = [row for row in report["groups"] if "pass_k" in row]
    if consistent:
        lines += ["", "## Consistency across trials", "", "| model | N | arm | k | trials | pass^k | agreement |",
                  "|" + "---|" * 7]
        for row in consistent:
            lines.append(f"| {row['model']} | {row['size']} | {row['arm']} | {row['k']} | {row['trials']} | "
                         f"{_pct(row['pass_k'])} | {_pct(row['agreement'])} |")
    lines += ["", "## Sources", ""]
    for source in sorted({(r["file"], r["date"], r["commit"]) for r in report["groups"]}):
        lines.append(f"- `{source[0]}` ({source[1]}, commit {source[2]})")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarise routing result files.")
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    paths = args.paths or sorted(p for p in RESULTS_DIR.glob("*.json") if p.name != "RESULTS.json")
    if not paths:
        print("no result files")
        return 1
    report = build(load_documents(paths))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "RESULTS.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    (args.out_dir / "RESULTS.md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    print(f"wrote {args.out_dir / 'RESULTS.md'} from {len(paths)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
