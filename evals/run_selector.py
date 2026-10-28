"""Selector eval (plan 4.3 L1): how often the shortlist ranking puts the right agents in front. Offline, free.

For each case and roster size N, every agent in the case's roster gets a card built from its pool record
(the same text the embeddings cache holds) and each scorer ranks the whole roster for the case's query.
recall@k counts a case when all of its target agents rank within k; MRR uses the best-ranked target.
Should-create cases have no target and are reported apart, as the best score any agent reached.

    python -m evals.run_selector            # writes evals/results/<date>_selector_<sha>.json and SELECTOR.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from evals.build_embedding_cache import case_query, pool_log
from evals.generate import load_cases, load_pool, roster_for
from evals.run_routing import RESULTS_DIR, _git_sha
from server.config import get_settings
from server.services.execution.cards import AgentCard, build_card, card_text
from server.services.execution.embeddings import EmbeddingCache
from server.services.execution.selector import render_shortlist, select

CACHE_PATH = Path(__file__).resolve().parent / "fixtures" / "embeddings_cache.bin"
KS = (3, 5, 8, 12, 16)
SIZES = (5, 50, 500, 5000)
EVAL_NOW = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
# report key -> (scorers argument, max_pins)
SCORERS: Dict[str, tuple] = {
    "recency": ("recency", 0),
    "bm25": ("bm25", 0),
    "bm25_entities": ("bm25+entities", 0),
    "bm25_entities_recency": ("bm25+entities+recency", 0),
    "plus_pins": ("bm25+entities+recency", 4),
    "hybrid": ("hybrid", 0),
}
DEFAULT_SCORER = "plus_pins"


def pool_cards(pool: Dict[str, Dict[str, Any]], now: datetime = EVAL_NOW) -> Dict[str, AgentCard]:
    """One card per pool agent, dated from its `last_active_days_ago`."""
    cards = {}
    for agent_id, record in pool.items():
        source = SimpleNamespace(
            id=agent_id, name=record["name"], status=record.get("status", "active"),
            last_active_at=(now - timedelta(days=record["last_active_days_ago"])).isoformat(),
            purpose="", entities=[],
        )
        cards[agent_id] = build_card(source, pool_log(record))
    return cards


def tail_text(case: Dict[str, Any]) -> str:
    """The last three turns, which is what pinning reads."""
    return "\n".join(turn["text"] for turn in case["history"][-3:])


def rank(case: Dict[str, Any], cards: Sequence[AgentCard], scorer: str,
         vectors: Optional[Dict[str, List[float]]] = None) -> List[tuple]:
    """Every card in the roster as (agent_id, score), best first, with no k or token-budget cut."""
    scorers, max_pins = SCORERS[scorer]
    extra = {}
    if scorers == "hybrid":
        extra = {"vectors": vectors, "query_vector": vectors[f"query:{case['case_id']}"]}
    chosen = select(case_query(case), cards, tail_text=tail_text(case), k=len(cards), max_pins=max_pins,
                    token_budget=float("inf"), now=EVAL_NOW, scorers=scorers, **extra)
    return [(item.card.agent_id, item.score) for item in chosen]


def load_vectors(cache_path: Path, cards: Dict[str, AgentCard], cases: List[Dict[str, Any]]) -> Optional[dict]:
    """Agent and query vectors from the committed cache, or None when any is missing."""
    if not cache_path.exists():
        return None
    cache = EmbeddingCache(cache_path)
    vectors = {agent_id: cache.get(card_text(card)) for agent_id, card in cards.items()}
    vectors.update({f"query:{case['case_id']}": cache.get(case_query(case)) for case in cases})
    return None if any(v is None for v in vectors.values()) else vectors


def roster_tokens(case: Dict[str, Any], roster: List[Dict[str, Any]], cards: Sequence[AgentCard]) -> Dict[str, float]:
    """Offline token estimate (chars / 4) of the roster block: every name (baseline) vs the live shortlist."""
    settings = get_settings()
    full = "\n".join(f'<agent name="{escape(a["name"], quote=True)}" />' for a in roster)
    shortlist = select(case_query(case), cards, tail_text=tail_text(case), k=settings.shortlist_k,
                       token_budget=settings.shortlist_token_budget, now=EVAL_NOW)
    return {"full": len(full) / 4, "shortlist": len(render_shortlist(shortlist, len(roster))) / 4}


def evaluate(cases: List[Dict[str, Any]], sizes: Sequence[int] = SIZES, cache_path: Path = CACHE_PATH,
             scorers: Sequence[str] = tuple(SCORERS)) -> Dict[str, Any]:
    pool = load_pool()
    all_cards = pool_cards(pool)
    vectors = load_vectors(cache_path, all_cards, cases) if "hybrid" in scorers else None
    scorers = [s for s in scorers if s != "hybrid" or vectors is not None]
    rows = []
    for case in cases:
        for size in sizes:
            roster = roster_for(case, size, pool=pool)
            cards = [all_cards[a["id"]] for a in roster]
            row = {"case_id": case["case_id"], "category": case["category"], "split": case["split"],
                   "size": size, "tokens": roster_tokens(case, roster, cards), "ranks": {}, "best_score": {}}
            for scorer in scorers:
                ranking = rank(case, cards, scorer, vectors)
                positions = {agent_id: i + 1 for i, (agent_id, _) in enumerate(ranking)}
                row["ranks"][scorer] = [positions[t] for t in case["target_ids"]]
                row["best_score"][scorer] = ranking[0][1]
            rows.append(row)
    return {"rows": rows, "summary": summarise(rows, scorers, sizes), "hybrid": vectors is not None}


def _recall(rows: List[Dict[str, Any]], scorer: str) -> Dict[str, Any]:
    ranked = [r for r in rows if r["ranks"][scorer]]
    if not ranked:
        return {"n": 0}
    out = {"n": len(ranked)}
    for k in KS:
        out[f"k{k}"] = sum(max(r["ranks"][scorer]) <= k for r in ranked) / len(ranked)
    out["mrr"] = sum(1 / min(r["ranks"][scorer]) for r in ranked) / len(ranked)
    return out


def summarise(rows: List[Dict[str, Any]], scorers: Sequence[str], sizes: Sequence[int]) -> Dict[str, Any]:
    by_size = defaultdict(list)
    for r in rows:
        by_size[r["size"]].append(r)
    summary: Dict[str, Any] = {"recall": {}, "tokens": {}, "create_best_score": {}, "target_score": {}}
    for scorer in scorers:
        summary["recall"][scorer] = {}
        for size in sizes:
            subset = by_size[size]
            entry = _recall(subset, scorer)
            entry["non_ambiguous"] = _recall([r for r in subset if r["category"] != "ambiguous"], scorer)
            entry["by_category"] = {cat: _recall([r for r in subset if r["category"] == cat], scorer)
                                    for cat in sorted({r["category"] for r in subset})
                                    if cat != "should_create"}
            summary["recall"][scorer][f"n{size}"] = entry
            creates = [r["best_score"][scorer] for r in subset if not r["ranks"][scorer]]
            summary["create_best_score"].setdefault(scorer, {})[f"n{size}"] = (
                {"mean": statistics.fmean(creates), "median": statistics.median(creates), "n": len(creates)}
                if creates else None)
    for size in sizes:
        full = [r["tokens"]["full"] for r in by_size[size]]
        short = [r["tokens"]["shortlist"] for r in by_size[size]]
        summary["tokens"][f"n{size}"] = {"full_mean": statistics.fmean(full), "shortlist_mean": statistics.fmean(short),
                                         "ratio": statistics.fmean(short) / statistics.fmean(full)}
    return summary


def render_markdown(document: Dict[str, Any]) -> str:
    header, summary = document["header"], document["summary"]
    lines = ["# Selector results (L1, offline)", "",
             f"Generated by `python -m evals.run_selector` on {header['date']}, commit {header['commit']}, "
             f"{header['n_cases']} cases ({header['split']} split), {header['n_ranked']} with a target agent. "
             "recall@k: all target agents rank within k. MRR: best-ranked target. No token budget is applied "
             f"to the rankings; the live shortlist (and the size estimate below) stops at k={header['shortlist_k']} "
             f"or {header['token_budget']} tokens (OPENPOKE_SHORTLIST_K, OPENPOKE_SHORTLIST_TOKEN_BUDGET).", ""]
    if not document["hybrid"]:
        lines += ["The hybrid row is missing: the embeddings cache does not cover every card and query.", ""]
    for size in header["sizes"]:
        lines += [f"## N = {size}", "", "| scorer | n | " + " | ".join(f"recall@{k}" for k in KS)
                  + " | MRR | recall@8 non-ambiguous |", "|" + "---|" * (len(KS) + 4)]
        for scorer, by_n in summary["recall"].items():
            r = by_n[f"n{size}"]
            lines.append(f"| {scorer} | {r['n']} | " + " | ".join(f"{r[f'k{k}']:.3f}" for k in KS)
                         + f" | {r['mrr']:.3f} | {r['non_ambiguous']['k8']:.3f} |")
        lines.append("")
    default = summary["recall"][DEFAULT_SCORER]
    categories = sorted(default[f"n{header['sizes'][0]}"]["by_category"])
    lines += [f"## recall@8 by category ({DEFAULT_SCORER})", "", "| N | " + " | ".join(categories) + " |",
              "|" + "---|" * (len(categories) + 1)]
    for size in header["sizes"]:
        by_cat = default[f"n{size}"]["by_category"]
        lines.append(f"| {size} | " + " | ".join(f"{by_cat[c]['k8']:.2f} ({by_cat[c]['n']})" for c in categories) + " |")
    lines += ["", "## Roster block size (chars / 4, mean per case)", "",
              f"| N | every name (baseline) | k={header['shortlist_k']} shortlist | shortlist / full |",
              "|---|---|---|---|"]
    for size in header["sizes"]:
        t = summary["tokens"][f"n{size}"]
        lines.append(f"| {size} | {t['full_mean']:.0f} | {t['shortlist_mean']:.0f} | {t['ratio']:.2f} |")
    lines += ["", f"## Should-create cases: best score any agent reached ({DEFAULT_SCORER})", "",
              "| N | mean | median | n |", "|---|---|---|---|"]
    for size in header["sizes"]:
        c = summary["create_best_score"][DEFAULT_SCORER][f"n{size}"]
        if c:
            lines.append(f"| {size} | {c['mean']:.2f} | {c['median']:.2f} | {c['n']} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Offline selector eval (recall@k, MRR, roster size).")
    parser.add_argument("--sizes", default=",".join(map(str, SIZES)))
    parser.add_argument("--split", choices=["report", "heldout", "all"], default="all")
    parser.add_argument("--cache", type=Path, default=CACHE_PATH)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    sizes = tuple(int(s) for s in args.sizes.split(","))
    cases = load_cases(split=args.split)
    result = evaluate(cases, sizes, args.cache)
    header = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "commit": _git_sha(),
              "split": args.split, "n_cases": len(cases), "n_ranked": sum(bool(c["target_ids"]) for c in cases),
              "sizes": list(sizes), "ks": list(KS), "scorers": list(result["summary"]["recall"]),
              "now": EVAL_NOW.isoformat(), "shortlist_k": get_settings().shortlist_k,
              "token_budget": get_settings().shortlist_token_budget}
    document = {"header": header, "hybrid": result["hybrid"], "summary": result["summary"], "rows": result["rows"]}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    path = args.out_dir / f"{header['date'][:10].replace('-', '')}_selector_{header['commit']}.json"
    path.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n")
    (args.out_dir / "SELECTOR.md").write_text(render_markdown(document), encoding="utf-8", newline="\n")
    r = result["summary"]["recall"][DEFAULT_SCORER]
    for size in sizes:
        print(f"N={size}: recall@8 {r[f'n{size}']['k8']:.3f}, non-ambiguous {r[f'n{size}']['non_ambiguous']['k8']:.3f},"
              f" MRR {r[f'n{size}']['mrr']:.3f}")
    print(f"wrote {path} and SELECTOR.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
