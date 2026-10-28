"""Sweep the duplicate guard's name-similarity threshold on the held-out split. Offline, free.

Two kinds of mistakes, counted at each threshold:
- missed merge: on a should-reuse case the baseline model created a new agent; the guard misses it when the
  new name's similarity to the gold agent's name is below the threshold.
- false merge: the guard flags an agent of a different matter: on a should-create case, the name the model
  created is at least the threshold close to some agent in the roster; or, on a near-duplicate case, the
  gold agent's name is that close to its distractor's (a new agent for one would be pointed at the other).

Names come from the committed baseline results (what the model actually typed) and the generator's pairs.
Only the name score is swept; the guard's shared-id check does not use the threshold.

    python -m evals.tune_duplicate_threshold   # writes evals/results/DUPLICATE_THRESHOLD.md and .json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from evals.generate import load_cases, load_pool, roster_for
from evals.run_routing import RESULTS_DIR
from server.agents.interaction_agent.tools import name_similarity

THRESHOLDS = [round(0.50 + 0.05 * i, 2) for i in range(10)]


def collect(records: List[Dict[str, Any]], cases: Dict[str, Dict[str, Any]],
            pool: Dict[str, Dict[str, Any]]) -> Dict[str, List[float]]:
    """Similarity scores for each kind of pair on the held-out split."""
    same_matter, new_matter, distractors = [], [], []
    for r in records:
        case = cases.get(r["case_id"])
        if case is None or case["split"] != "heldout":
            continue
        created = [x["name"] for x in r["resolved"] if x["action"] == "create"]
        if r["cell"] == "duplicate":
            same_matter += [max(name_similarity(n, pool[g]["name"]) for g in case["gold"]["agent_ids"]) for n in created]
        if case["gold"]["kind"] == "create" and created:
            roster = [a["name"] for a in roster_for(case, r["size"], pool=pool)]
            new_matter += [max(name_similarity(n, other) for other in roster) for n in created]
    for case in cases.values():
        if case["split"] == "heldout" and case["category"] == "near_duplicate":
            for gold in case["gold"]["agent_ids"]:
                distractors += [name_similarity(pool[gold]["name"], pool[o]["name"]) for o in case["hard_negative_ids"]]
    return {"same_matter": same_matter, "new_matter": new_matter, "distractor": distractors}


def sweep(scores: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    rows = []
    for t in THRESHOLDS:
        missed = sum(s < t for s in scores["same_matter"])
        false = sum(s >= t for s in scores["new_matter"]) + sum(s >= t for s in scores["distractor"])
        rows.append({"threshold": t, "missed_merges": missed, "false_merges": false, "total": missed + false})
    return rows


def choose(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Fewest mistakes; on a tie the higher threshold, which interrupts the model less."""
    return min(rows, key=lambda row: (row["total"], -row["threshold"]))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=None, help="a baseline result file (default: newest)")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    path = args.results or sorted(RESULTS_DIR.glob("*_baseline_*.json"))[-1]
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    scores = collect(records, {c["case_id"]: c for c in load_cases()}, load_pool())
    rows = sweep(scores)
    best = choose(rows)
    document = {"source": path.name, "split": "heldout", "pairs": {k: len(v) for k, v in scores.items()},
                "sweep": rows, "chosen": best}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "DUPLICATE_THRESHOLD.json").write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8",
                                                           newline="\n")
    lines = ["# Duplicate-guard name threshold (held-out split)", "",
             f"From `{path.name}`: {len(scores['same_matter'])} names the model created for an agent that already "
             f"existed, {len(scores['new_matter'])} names it created for a genuinely new matter, and "
             f"{len(scores['distractor'])} near-duplicate pairs from the generator. Only the name score is swept.",
             "", "| threshold | missed merges | false merges | total |", "|---|---|---|---|"]
    lines += [f"| {r['threshold']:.2f} | {r['missed_merges']} | {r['false_merges']} | {r['total']} |" for r in rows]
    plateau = [r["threshold"] for r in rows if r["total"] == best["total"]]
    highest = max(scores["same_matter"], default=0.0)
    lines += ["", f"Chosen: {best['threshold']:.2f} (fewest mistakes; ties go to the higher threshold, which "
              "interrupts the model less). The server default is not changed by this script.",
              f"Every threshold from {min(plateau):.2f} to {max(plateau):.2f} gives the same counts. The closest "
              f"a duplicate name came to its gold agent's name was {highest:.2f}, so the name score alone catches "
              "none of the duplicates the model made here; the guard's shared-id check is what can."]
    (args.out_dir / "DUPLICATE_THRESHOLD.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
