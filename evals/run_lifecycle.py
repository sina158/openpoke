"""The 30-day lifecycle simulation (plan 4.3 L3): one broker's month through the real interaction turn.

For each simulated day the clock moves to 07:00 and the lifecycle hook runs (it archives only in the
shortlist modes, as in the code); then each of the day's messages goes through the real runtime with
execution agents stubbed (`evals/simulate.py`). The roster grows from what the model actually created
and reused. Each turn is graded against the scenario's gold (`evals/lifecycle_scenario.py`).

    python -m evals.run_lifecycle --arms baseline,fix_v2 --replay [--days 15] [--model M]

Writes `evals/results/lifecycle_<arm>_<model>_d<days>.jsonl` (one record per turn), merges the arm's
summary and per-day series into `evals/results/LIFECYCLE.json`, and renders `LIFECYCLE.md` from it.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from evals.cassette import CASSETTE_DIR, Cassette, CassetteMiss, LiveCallLimit
from evals.lifecycle_scenario import DAYS, build_scenario, ideal_timeline, tick_time
from evals.run_routing import (DEFAULT_MODEL, FIRST_CALLS_CHECKED, ProviderFailure, _git_sha, _model_slug,
                                default_model)
from evals.simulate import ARMS, HISTORY_ENTRIES, Simulation

RESULTS_DIR = Path(__file__).resolve().parent / "results"
SUMMARY_JSON = "LIFECYCLE.json"
SUMMARY_MD = "LIFECYCLE.md"
DEFAULT_ARMS = ("baseline", "fix_v2")
CHARS_PER_TOKEN = 4  # only to express the execution prompt's size in rough tokens; nothing is sent


# ---- running ------------------------------------------------------------------------------------------------

def simulate(arm: str, model: Optional[str] = None, mode: str = "replay", days: int = DAYS, k: int = 8,
             cassette_dir: Path = CASSETTE_DIR, inner: Optional[Callable[..., Any]] = None,
             scenario: Optional[Dict[str, Any]] = None, max_live_calls: Optional[int] = None) -> Dict[str, Any]:
    """Run one arm over the first `days` days; return {header, seeded, names, records, days, exec_prompt}."""
    scenario = scenario or build_scenario()
    model = model or (DEFAULT_MODEL if mode == "replay" else default_model(os.environ.get("OPENPOKE_LLM_BASE_URL")))
    cassette = Cassette(f"lifecycle_{arm}_{_model_slug(model)}_k{k}", mode, cassette_dir, inner=inner,
                        max_live_calls=max_live_calls)
    sim = Simulation(arm, model, cassette, k=k)
    started = time.perf_counter()
    records: List[Dict[str, Any]] = []
    series: List[Dict[str, Any]] = []
    with sim.running():
        seeded = {sim.seed_agent(row["name"], row["first_request"], row["last_response"],
                                 datetime.fromisoformat(row["last_active_at"])): row["matter_id"]
                  for row in scenario["seed_roster"]}
        for day in range(1, days + 1):
            archived = sim.tick(tick_time(day))
            for message in (m for m in scenario["messages"] if m["day"] == day):
                turn = sim.turn(message["text"], datetime.fromisoformat(message["at"]))
                records.append({key: message[key] for key in ("msg_id", "day", "client_key", "gold")}
                               | turn.to_dict())
                first = records[:FIRST_CALLS_CHECKED]
                if len(records) == FIRST_CALLS_CHECKED and all(_provider_error(r["error"]) for r in first):
                    raise ProviderFailure(f"the first {FIRST_CALLS_CHECKED} turns all failed: {first[0]['error']}")
            statuses = sim.statuses()
            series.append({"day": day, "tick_archived": archived,
                           "active": sum(s == "active" for s in statuses.values()),
                           "archived": sum(s == "archived" for s in statuses.values())})
        last_day_agents = sorted({s["agent_id"] for r in records if r["day"] == days for s in r["sends"]})
        exec_prompt = sim.execution_prompt_chars(last_day_agents)
        names = sim.names()
    header = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "arm": arm,
        **{key.lower(): value for key, value in ARMS[arm].items()}, "model": model, "k": k, "days": days,
        "seed": scenario["seed"], "mode": mode, "commit": _git_sha(), "turns": len(records),
        "cassette": cassette.path.name, "cassette_hits": cassette.hits, "live_calls": cassette.live_calls,
        "history_entries": HISTORY_ENTRIES, "seconds": round(time.perf_counter() - started, 1),
    }
    return {"header": header, "seeded": seeded, "names": names, "records": records, "days": series,
            "exec_prompt": exec_prompt}


def _provider_error(error: Optional[str]) -> bool:
    """A failed call at the provider, not the model running out of tool iterations."""
    return bool(error) and "iteration limit" not in error


# ---- scoring (pure) -----------------------------------------------------------------------------------------

def grade_turns(records: List[Dict[str, Any]], seeded: Dict[str, str],
                matters: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Label every send, in order, against the gold. Each agent belongs to the matter it was created for.

    Per turn: {duplicates, wrong_reuse, cross_client, correct_reuse, correct_create, ambiguous_guess,
    revive_due, revived, clarified, over_clarified, no_route, needed}. `needed` maps the message's matter
    (each candidate's, when ambiguous) to its agents at the start of the turn.
    """
    owner: Dict[str, Tuple[Optional[str], str]] = {
        agent_id: (matter_id, matters[matter_id]["client_key"]) for agent_id, matter_id in seeded.items()}
    agents_of: Dict[str, List[str]] = defaultdict(list)
    for agent_id, matter_id in seeded.items():
        agents_of[matter_id].append(agent_id)
    graded = []
    for record in records:
        gold, client = record["gold"], record["client_key"]
        ambiguous = gold["kind"] == "ambiguous"
        matter = None if ambiguous else gold["matter_ids"][0]
        needed = {m: list(agents_of[m]) for m in gold["matter_ids"] if agents_of.get(m)}
        archived = set(record["archived_at_start"])
        own = needed.get(matter, [])
        g = {"duplicates": 0, "wrong_reuse": 0, "cross_client": 0, "correct_reuse": 0, "correct_create": 0,
             "ambiguous_guess": 0, "revived": 0, "needed": needed,
             "revive_due": int(gold["kind"] == "matter" and bool(own) and all(a in archived for a in own))}
        for send in record["sends"]:
            agent_id = send["agent_id"]
            if send["created"]:
                if ambiguous or agents_of.get(matter):
                    g["duplicates"] += 1
                else:
                    g["correct_create"] += 1
                owner[agent_id] = (matter, client)
                if matter:
                    agents_of[matter].append(agent_id)
                continue
            own_matter, own_client = owner.get(agent_id, (None, ""))
            if own_matter is not None and own_matter in gold["matter_ids"]:
                if ambiguous:
                    g["ambiguous_guess"] += 1
                else:
                    g["correct_reuse"] += 1
                    g["revived"] += int(agent_id in archived)
            else:
                g["wrong_reuse"] += 1
                g["cross_client"] += int(own_client != client)
        g["revived"] = min(g["revived"], g["revive_due"])
        g["clarified"] = int(bool(record["clarified"]) and ambiguous)
        g["over_clarified"] = int(bool(record["clarified"]) and not ambiguous)
        g["no_route"] = int(not record["sends"] and not record["clarified"])
        graded.append(g)
    return graded


def slope(points: List[Tuple[float, float]]) -> Optional[float]:
    """Least-squares slope of y on x, or None with fewer than two distinct x."""
    if len({x for x, _ in points}) < 2:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    num = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return num / sum((x - mean_x) ** 2 for x, _ in points)


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def score(run: Dict[str, Any], scenario: Dict[str, Any]) -> Dict[str, Any]:
    """One arm's lifecycle measures, plus the per-day series."""
    records, days = run["records"], run["header"]["days"]
    graded = grade_turns(records, run["seeded"], scenario["matters"])
    ideal = ideal_timeline(scenario, days=days)
    totals = {key: sum(g[key] for g in graded) for key in
              ("duplicates", "wrong_reuse", "cross_client", "correct_reuse", "correct_create", "ambiguous_guess",
               "revive_due", "revived", "clarified", "over_clarified", "no_route")}
    series, running = [], defaultdict(int)
    for row in run["days"]:
        day = row["day"]
        today = [(r, g) for r, g in zip(records, graded) if r["day"] == day]
        for key in ("duplicates", "wrong_reuse", "cross_client"):
            running[key] += sum(g[key] for _, g in today)
        series.append({
            "day": day, "active": row["active"], "archived": row["archived"], "ideal_live": ideal["live"][day - 1],
            "turns": len(today), "cum_duplicates": running["duplicates"], "cum_wrong_reuse": running["wrong_reuse"],
            "cum_cross_client": running["cross_client"],
            "prompt_tokens_per_turn": _mean([r["prompt_tokens"] for r, _ in today]),
            "prompt_chars_per_turn": _mean([r["prompt_chars"] for r, _ in today]),
            "searches_per_turn": _mean([r["searches"] for r, _ in today]),
        })
    last = [(r, g) for r, g in zip(records, graded) if r["day"] == days and g["needed"]]
    misses = [{"msg_id": r["msg_id"], "gold": r["gold"],
               "needed": sorted(a for ids in g["needed"].values() for a in ids),
               "archived": sorted(a for ids in g["needed"].values() for a in ids if a in r["archived_at_start"]),
               "shown": len(r["shown_ids"])}
              for r, g in last if not all(set(ids) & set(r["shown_ids"]) for ids in g["needed"].values())]
    exec_sizes = list(run["exec_prompt"].values())
    archive_events = sum(len(row["tick_archived"]) for row in run["days"]) + sum(len(r["archived"]) for r in records)
    return {
        "turns": len(records),
        "llm_calls": sum(r["llm_calls"] for r in records),
        "errors": sum(1 for r in records if r["error"]),
        **totals,
        "revive_rate": round(totals["revived"] / totals["revive_due"], 3) if totals["revive_due"] else None,
        "clarifications": sum(1 for r in records if r["clarified"]),
        "ambiguous_messages": sum(1 for r in records if r["gold"]["kind"] == "ambiguous"),
        "archive_events": archive_events,
        "revive_events": sum(len(r["revived"]) for r in records),
        "ideal_archive_events": len(ideal["archives"]),
        "ideal_revive_events": len(ideal["revives"]),
        "live_end": series[-1]["active"] if series else 0,
        "ideal_live_end": ideal["live"][days - 1] if days else 0,
        "live_minus_ideal_mean": _mean([row["active"] - row["ideal_live"] for row in series]),
        "prompt_tokens_per_turn": _mean([r["prompt_tokens"] for r in records]),
        "prompt_tokens_slope_per_day": _round(slope([(row["day"], row["prompt_tokens_per_turn"]) for row in series
                                                     if row["prompt_tokens_per_turn"] is not None])),
        "prompt_chars_slope_per_day": _round(slope([(row["day"], row["prompt_chars_per_turn"]) for row in series
                                                    if row["prompt_chars_per_turn"] is not None])),
        "searches_per_turn": _mean([r["searches"] for r in records]),
        "reachability_last_day": {"day": days, "needing_an_agent": len(last), "reachable": len(last) - len(misses),
                                  "misses": misses},
        "exec_prompt_last_day": {"agents": len(exec_sizes), "mean_chars": _mean(exec_sizes),
                                 "max_chars": max(exec_sizes, default=None),
                                 "mean_tokens_approx": _mean([c / CHARS_PER_TOKEN for c in exec_sizes])},
        "series": series,
    }


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


# ---- output -------------------------------------------------------------------------------------------------

def write_outputs(run: Dict[str, Any], summary: Dict[str, Any], directory: Path = RESULTS_DIR) -> Path:
    """Write the arm's per-turn JSONL, merge its summary into LIFECYCLE.json and re-render LIFECYCLE.md."""
    header = run["header"]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"lifecycle_{header['arm']}_{_model_slug(header['model'])}_d{header['days']}.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"header": header, "seeded": run["seeded"], "names": run["names"]},
                                ensure_ascii=False) + "\n")
        for record in run["records"]:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary_path = directory / SUMMARY_JSON
    document = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {"arms": {}}
    document["arms"][header["arm"]] = {"header": header, "summary": summary}
    summary_path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                            newline="\n")
    (directory / SUMMARY_MD).write_text(render_markdown(document), encoding="utf-8", newline="\n")
    return path


_HEADLINE = (("turns", "turns"), ("llm_calls", "model calls"), ("errors", "errors"),
             ("live_end", "live agents at the end"), ("ideal_live_end", "ideal live at the end"),
             ("live_minus_ideal_mean", "live minus ideal, daily mean"), ("duplicates", "duplicates"),
             ("wrong_reuse", "wrong reuse"), ("cross_client", "cross-client sends"),
             ("revive_rate", "revive rate"), ("revive_due", "messages for an archived matter"),
             ("clarifications", "clarifications"), ("clarified", "clarified when ambiguous"),
             ("over_clarified", "clarified when not ambiguous"), ("ambiguous_guess", "guessed when ambiguous"),
             ("no_route", "no route"), ("prompt_tokens_per_turn", "prompt tokens per turn"),
             ("prompt_tokens_slope_per_day", "prompt tokens per turn, slope per day"),
             ("prompt_chars_slope_per_day", "prompt chars per turn, slope per day"),
             ("searches_per_turn", "searches per turn"), ("archive_events", "archive events"),
             ("revive_events", "revive events"))


def render_markdown(document: Dict[str, Any]) -> str:
    """One table of headline measures across arms, then each arm's reachability misses and per-day series."""
    arms = [arm for arm in ARMS if arm in document["arms"]]
    if not arms:
        return "# Lifecycle simulation\n\nNo runs yet.\n"
    entries = [document["arms"][arm] for arm in arms]
    lines = ["# Lifecycle simulation", "",
             "One broker's month (`evals/lifecycle_scenario.py`, seed "
             f"{entries[0]['header']['seed']}) through the real interaction turn with execution agents stubbed.",
             "", "| measure | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    lines.append("| run | " + " | ".join(f"{e['header']['model']}, {e['header']['days']} days, {e['header']['mode']}"
                                         for e in entries) + " |")
    for key, label in _HEADLINE:
        lines.append(f"| {label} | " + " | ".join(str(e["summary"].get(key)) for e in entries) + " |")
    lines.append("| ideal archive / revive events | " + " | ".join(
        f"{e['summary']['ideal_archive_events']} / {e['summary']['ideal_revive_events']}" for e in entries) + " |")
    for arm, entry in zip(arms, entries):
        summary = entry["summary"]
        reach, execp = summary["reachability_last_day"], summary["exec_prompt_last_day"]
        lines += ["", f"## {arm}", "",
                  f"Day {reach['day']}: {reach['reachable']} of {reach['needing_an_agent']} messages needing an "
                  "existing agent had it in the prompt's roster or shortlist."]
        lines += [f"- missed {miss['msg_id']} ({', '.join(miss['gold']['matter_ids'])}): needed "
                  f"{', '.join(miss['needed'])}" + (f", archived {', '.join(miss['archived'])}" if miss["archived"]
                                                    else "") + f"; {miss['shown']} shown"
                  for miss in reach["misses"]]
        lines += [f"Execution prompt on day {reach['day']} (measured, not sent): {execp['agents']} agents, mean "
                  f"{execp['mean_chars']} chars (~{execp['mean_tokens_approx']} tokens), max {execp['max_chars']}.",
                  "", "| day | live | archived | ideal live | duplicates | wrong reuse | cross-client | "
                  "prompt tokens/turn | prompt chars/turn | searches/turn |", "|---" * 10 + "|"]
        lines += [f"| {r['day']} | {r['active']} | {r['archived']} | {r['ideal_live']} | {r['cum_duplicates']} | "
                  f"{r['cum_wrong_reuse']} | {r['cum_cross_client']} | {r['prompt_tokens_per_turn']} | "
                  f"{r['prompt_chars_per_turn']} | {r['searches_per_turn']} |" for r in summary["series"]]
    return "\n".join(lines) + "\n"


_LABELS = (("correct_create", "new agent, right"), ("correct_reuse", "reused the right agent"),
           ("revived", "revived"), ("duplicates", "DUPLICATE"), ("wrong_reuse", "WRONG REUSE"),
           ("cross_client", "CROSS-CLIENT"), ("ambiguous_guess", "guessed"), ("clarified", "clarified"),
           ("over_clarified", "clarified needlessly"), ("no_route", "no route"))


def render_transcript(run: Dict[str, Any], scenario: Dict[str, Any]) -> str:
    """The arm's month turn by turn: message, gold, the routing calls made and how each was graded."""
    header, names, matters = run["header"], run["names"], scenario["matters"]
    graded = grade_turns(run["records"], run["seeded"], matters)
    lines = [f"# Lifecycle transcript: {header['arm']}", "",
             f"Model `{header['model']}`, {header['days']} days, {header['mode']}; agents are named as at the end."]
    for row in run["days"]:
        lines += ["", f"## Day {row['day']}: {row['active']} live, {row['archived']} archived", ""]
        if row["tick_archived"]:
            lines.append("Archived at 07:00: " + ", ".join(names.get(i, i) for i in row["tick_archived"]) + ".")
            lines.append("")
        for record, g in zip(run["records"], graded):
            if record["day"] != row["day"]:
                continue
            gold = record["gold"]
            wanted = " or ".join(matters[m].get("agent_name", m) for m in gold["matter_ids"])
            lines.append(f"- **{record['msg_id']}** {record['at'][11:16]} \"{record['text']}\" "
                         f"(gold: {gold['kind']}, {wanted})")
            for call in record["tool_calls"]:
                args = call["arguments"] or {}
                target = next((args[key] for key in ("agent_id", "agent_name", "candidate_ids", "query")
                               if args.get(key)), "")
                result = call["result"] if isinstance(call["result"], dict) else {}
                landed = names.get(result.get("agent_id", ""), "")
                lines.append(f"  - `{call['tool']}` {target!r} -> {call['status']}"
                             + (f" ({landed})" if landed else ""))
            labels = [label for key, label in _LABELS if g.get(key)]
            lines.append(f"  - graded: {', '.join(labels) or 'nothing routed'}")
    return "\n".join(lines) + "\n"


def summary_line(arm: str, summary: Dict[str, Any]) -> str:
    return (f"{arm}: {summary['turns']} turns, live {summary['live_end']} (ideal {summary['ideal_live_end']}), "
            f"duplicates {summary['duplicates']}, wrong reuse {summary['wrong_reuse']} "
            f"(cross-client {summary['cross_client']}), revive {summary['revived']}/{summary['revive_due']}, "
            f"clarifications {summary['clarifications']}, prompt tokens/turn {summary['prompt_tokens_per_turn']} "
            f"(slope {summary['prompt_tokens_slope_per_day']}/day), searches/turn {summary['searches_per_turn']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate a month of one owner's messages per arm.")
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS), help=f"comma-separated, from {', '.join(ARMS)}")
    parser.add_argument("--days", type=int, default=DAYS, help="simulate the first N days (15 halves the calls)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--k", type=int, default=8, help="shortlist size")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", dest="mode", action="store_const", const="replay")
    mode.add_argument("--record", dest="mode", action="store_const", const="record")
    mode.add_argument("--live", dest="mode", action="store_const", const="live")
    parser.set_defaults(mode="replay")
    parser.add_argument("--cassette-dir", type=Path, default=CASSETTE_DIR)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    parser.add_argument("--max-live-calls", type=int, default=None, help="stop a live run after this many calls")
    parser.add_argument("--transcript", action="store_true", help="also write each arm's month as Markdown")
    args = parser.parse_args(argv)
    arms = [arm.strip() for arm in args.arms.split(",") if arm.strip()]
    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown or not 1 <= args.days <= DAYS:
        parser.error(f"unknown arms {unknown}" if unknown else f"--days must be 1..{DAYS}")
    if args.mode != "replay" and not (os.environ.get("OPENPOKE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        parser.error("--record and --live need OPENPOKE_LLM_API_KEY or OPENROUTER_API_KEY")
    logging.getLogger("openpoke").setLevel(logging.WARNING)
    scenario = build_scenario()
    for arm in arms:
        try:
            run = simulate(arm, args.model, args.mode, args.days, args.k, args.cassette_dir, scenario=scenario,
                           max_live_calls=args.max_live_calls)
        except (CassetteMiss, LiveCallLimit, ProviderFailure) as exc:
            print(f"{arm}: stopped, nothing written: {exc}", file=sys.stderr)
            return 2
        summary = score(run, scenario)
        path = write_outputs(run, summary, args.out_dir)
        if args.transcript:
            path.with_suffix(".md").write_text(render_transcript(run, scenario), encoding="utf-8", newline="\n")
        print(summary_line(arm, summary))
        print(f"  live calls {run['header']['live_calls']}, cassette hits {run['header']['cassette_hits']}, "
              f"{run['header']['seconds']} s; wrote {path.name}, {SUMMARY_JSON}, {SUMMARY_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
