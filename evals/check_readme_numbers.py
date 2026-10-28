"""Check that every marked number in README.md equals its value in evals/results/RESULTS.json.

Mark a number in the README as `<!-- n:by_key.baseline_minimax-m3_k8_n500.accuracy.rate -->50.0%<!-- /n -->`.
The key is a dotted path into RESULTS.json (list items by index). A shown value ending in % is compared with the
JSON value times 100; numbers are compared at the precision the README shows; anything else must match as text.
Exits 1 on any mismatch or unknown key.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
MARKER = re.compile(r"<!--\s*n:([^\s>]+)\s*-->(.*?)<!--\s*/n\s*-->", re.DOTALL)
NUMBER = re.compile(r"([-+]?\d[\d,]*(?:\.(\d+))?)\s*(%?)")


def lookup(data: Any, path: str) -> Any:
    """Follow a dotted path; raises KeyError when any part is missing."""
    for part in path.split("."):
        if isinstance(data, list):
            if not part.isdigit() or int(part) >= len(data):
                raise KeyError(path)
            data = data[int(part)]
        elif isinstance(data, dict) and part in data:
            data = data[part]
        else:
            raise KeyError(path)
    return data


def expected_text(shown: str, value: Any) -> str:
    """Render `value` the way `shown` is written, so the two can be compared as text."""
    match = NUMBER.fullmatch(shown)
    if match and isinstance(value, (int, float)) and not isinstance(value, bool):
        decimals = len(match.group(2) or "")
        number = value * 100 if match.group(3) else value
        text = f"{number:,.{decimals}f}" if "," in match.group(1) else f"{number:.{decimals}f}"
        return text + match.group(3)
    return str(value)


def check(readme: str, results: Any) -> List[Tuple[str, str, str, str]]:
    """Return (key, shown, expected, status) for every marker; status is ok, MISMATCH or UNKNOWN KEY."""
    rows = []
    for key, raw in MARKER.findall(readme):
        shown = " ".join(raw.split())
        try:
            expected = expected_text(shown, lookup(results, key))
        except KeyError:
            rows.append((key, shown, "-", "UNKNOWN KEY"))
            continue
        rows.append((key, shown, expected, "ok" if shown == expected else "MISMATCH"))
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check README numbers against the results file.")
    parser.add_argument("--readme", type=Path, default=ROOT / "README.md")
    parser.add_argument("--results", type=Path, default=ROOT / "evals" / "results" / "RESULTS.json")
    args = parser.parse_args(argv)
    rows = check(args.readme.read_text(encoding="utf-8"), json.loads(args.results.read_text(encoding="utf-8")))
    width = max([len(key) for key, *_ in rows] + [3])
    for key, shown, expected, status in rows:
        print(f"{status:<11} {key:<{width}}  README {shown!r}  results {expected!r}")
    bad = [row for row in rows if row[3] != "ok"]
    print(f"{len(rows)} marked numbers checked, {len(bad)} wrong")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
