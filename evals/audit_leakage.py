"""Name-leak audit: how often does a case's message contain its gold agent's name verbatim?

If too many messages name the agent, routing reduces to string matching and the set is too easy.
Exits non-zero when the share is at or above the limit.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional

from evals.generate import load_cases, load_pool

LIMIT = 0.30


def leaked_cases(cases: List[Dict[str, Any]], pool: Dict[str, Dict[str, Any]]) -> List[str]:
    """Return ids of cases whose message contains a gold agent's name (case-insensitive)."""
    leaked = []
    for case in cases:
        message = case["message"].lower()
        if any(pool[agent_id]["name"].lower() in message for agent_id in case["gold"]["agent_ids"]):
            leaked.append(case["case_id"])
    return leaked


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=float, default=LIMIT)
    args = parser.parse_args(argv)
    cases, pool = load_cases(), load_pool()
    leaked = leaked_cases(cases, pool)
    share = len(leaked) / len(cases)
    print(f"name leak: {len(leaked)}/{len(cases)} cases = {share:.1%} (limit {args.limit:.0%})")
    if share >= args.limit:
        print("FAIL: too many messages contain the gold agent's name", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
