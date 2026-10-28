"""Routing eval (plan 4.3 L2): the real interaction prompt, tool schemas and loop, with tool handling recorded.

Each case gets its own data dir, seeded through the repo's own APIs (roster, execution logs, conversation
log), and its prompt is rendered there first. The loops then run concurrently. The first assistant turn that
calls a routing tool ends the case; every routing call in that turn is graded from its arguments.

    python -m evals.run_routing --arm baseline --sizes 5,50,500 --trials 3 --replay
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import importlib
import itertools
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

from evals.cassette import CURRENT, CallContext, Cassette, CassetteMiss, LiveCallLimit, CASSETTE_DIR
from evals.generate import SEED, _slug, load_cases, load_pool, roster_for

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_MODEL = "minimax-m3"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
ARMS: Dict[str, Dict[str, str]] = {
    "baseline": {"OPENPOKE_ROSTER_MODE": "full", "OPENPOKE_PROMPT_VARIANT": "original"},
    "prompt_only": {"OPENPOKE_ROSTER_MODE": "full", "OPENPOKE_PROMPT_VARIANT": "shortlist"},
    "fix": {"OPENPOKE_ROSTER_MODE": "shortlist", "OPENPOKE_PROMPT_VARIANT": "shortlist"},
}
ROUTING_TOOLS = {"send_message_to_agent", "clarify_agent_choice"}
CELLS = ("correct_reuse", "wrong_reuse", "duplicate", "over_clarify", "hijack", "correct_create",
         "correct_clarify", "wrong_guess", "partial", "no_route", "error")
CORRECT_CELLS = {"correct_reuse", "correct_create", "correct_clarify"}
EVAL_CLOCK = datetime(2026, 10, 27, 9, 0)
# The server's "now" (OPENPOKE_FIXED_NOW) during a run: recency and card ages are rendered from it.
EVAL_NOW = EVAL_CLOCK.replace(tzinfo=timezone.utc)
_CLOCKED_MODULES = ("server.services.conversation.log", "server.services.execution.log_store",
                    "server.services.conversation.summarization.working_memory_log")
# Stores added by later branches; reset when present.
_OPTIONAL_RESETS = (("server.services.approvals", "reset_approval_store"),)


class RoutingDecided(Exception):
    """Raised from inside the loop once an assistant turn has made its routing calls."""

    def __init__(self, calls: List[Tuple[str, Dict[str, Any]]]) -> None:
        super().__init__(f"{len(calls)} routing call(s)")
        self.calls = calls


# ---- grading (pure) ------------------------------------------------------------------------------------------

def resolve_call(name: str, args: Dict[str, Any], by_id: Dict[str, str], by_slug: Dict[str, str]) -> Dict[str, Any]:
    """Map one routing call to reuse(pool id) | create(name) | clarify(ids) | error.

    `by_id` maps every id the model may pass (pool ids and the registry's minted ids) to a pool id; `by_slug`
    maps slugified roster names to pool ids, because the baseline reuses an agent by repeating its name.
    """
    if "__invalid_arguments__" in args:
        return {"action": "error", "detail": str(args["__invalid_arguments__"])}
    if name == "clarify_agent_choice":
        candidates = args.get("candidate_ids")
        ids = [by_id.get(str(c), str(c)) for c in candidates] if isinstance(candidates, list) else []
        return {"action": "clarify", "agent_ids": ids}
    agent_id = args.get("agent_id")
    if isinstance(agent_id, str) and agent_id.strip():
        if agent_id in by_id:
            return {"action": "reuse", "agent_id": by_id[agent_id]}
        return {"action": "error", "detail": f"unknown agent_id {agent_id!r}"}
    agent_name = args.get("agent_name")
    if isinstance(agent_name, str) and agent_name.strip():
        match = by_slug.get(_slug(agent_name))
        if match:
            return {"action": "reuse", "agent_id": match}
        return {"action": "create", "name": agent_name}
    return {"action": "error", "detail": "neither agent_id nor agent_name given"}


def grade(case: Dict[str, Any], resolved: List[Dict[str, Any]], client_key_of: Dict[str, str]) -> Tuple[str, bool]:
    """Return (cell, cross_client) for one case from its resolved routing calls."""
    if any(r["action"] == "error" for r in resolved):
        return "error", False
    if not resolved:
        return "no_route", False
    gold_kind = case["gold"]["kind"]
    gold_ids = set(case["gold"]["agent_ids"])
    reused = [r["agent_id"] for r in resolved if r["action"] == "reuse"]
    created = any(r["action"] == "create" for r in resolved)
    clarified = any(r["action"] == "clarify" for r in resolved)
    case_keys = set(case["client_key"].split("|"))
    wrong = [agent_id for agent_id in reused if agent_id not in gold_ids]
    cross = any(client_key_of.get(a) and client_key_of[a] not in case_keys for a in wrong)

    if gold_kind == "clarify":
        if clarified and not reused and not created:
            return "correct_clarify", False
        return "wrong_guess", cross
    if gold_kind == "create":
        if reused:
            return "hijack", cross
        return ("over_clarify", False) if clarified else ("correct_create", False)
    if wrong:
        return "wrong_reuse", cross
    if created:
        return "duplicate", False
    if clarified:
        return "over_clarify", False
    return ("correct_reuse", False) if set(reused) == gold_ids else ("partial", False)


# ---- seeding and rendering (sequential, one data dir per case) ----------------------------------------------

def point_stores_at(path: Path) -> None:
    """Point every data store at `path` by resetting the lazy singletons that read `data_dir()`."""
    from server.services.conversation import log as conversation
    from server.services.conversation.summarization import working_memory_log as memory
    from server.services.execution import log_store, registry

    path.mkdir(parents=True, exist_ok=True)
    os.environ["OPENPOKE_DATA_DIR"] = str(path)
    registry.reset_agent_registry()
    log_store.reset_execution_agent_logs()
    conversation.reset_conversation_log()
    for module_name, helper in _OPTIONAL_RESETS:
        try:
            getattr(importlib.import_module(module_name), helper)()
        except (ImportError, AttributeError):
            continue
    # The working-memory log still fixes its path at import time.
    memory._WORKING_MEMORY_LOG_PATH = path / "conversation" / "poke_working_memory.log"
    memory._working_memory_log = None


def seed_case(case: Dict[str, Any], roster: List[Dict[str, Any]], now: datetime) -> Dict[str, str]:
    """Write the roster, each agent's log and the case history through the repo's APIs.

    Returns registry id -> pool id, so a reuse by the minted id can be graded.
    """
    from server.services.conversation import get_conversation_log
    from server.services.execution import get_execution_agent_logs
    from server.services.execution.registry import get_agent_registry

    registry, logs = get_agent_registry(), get_execution_agent_logs()
    id_map: Dict[str, str] = {}
    for agent in roster:
        record, _ = registry.get_or_create(agent["name"])
        registry.touch(record.id, now=now - timedelta(days=agent["last_active_days_ago"]))
        logs.record_request(agent["name"], agent["first_request"])
        logs.record_agent_response(agent["name"], agent["last_response"])
        id_map[record.id] = agent["id"]
    conversation = get_conversation_log()
    writers = {"user": conversation.record_user_message, "assistant": conversation.record_reply,
               "agent": conversation.record_agent_message}
    for turn in case["history"]:
        writers[turn["role"]](turn["text"])
    return id_map


@contextlib.contextmanager
def frozen_log_clock(start: datetime) -> Iterator[None]:
    """Stamp log entries with a fixed clock (one minute per entry), so a rendered prompt, and therefore its
    cassette key, does not change from one run to the next."""
    modules = [importlib.import_module(name) for name in _CLOCKED_MODULES]
    ticks = itertools.count()

    def fake_now(fmt: Optional[str] = None, *, default: str = "UTC") -> Any:
        moment = start + timedelta(minutes=next(ticks))
        return moment if fmt is None else moment.strftime(fmt)

    originals = [module.now_in_user_timezone for module in modules]
    for module in modules:
        module.now_in_user_timezone = fake_now
    try:
        yield
    finally:
        for module, original in zip(modules, originals):
            module.now_in_user_timezone = original


@contextlib.contextmanager
def fast_sqlite() -> Iterator[None]:
    """Skip fsync while seeding a throwaway sandbox (about 6x faster on Windows); the data is identical."""
    connect = sqlite3.connect

    def connect_without_sync(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = connect(*args, **kwargs)
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA journal_mode=MEMORY")
        return conn

    sqlite3.connect = connect_without_sync
    try:
        yield
    finally:
        sqlite3.connect = connect


@dataclass
class Rendered:
    case: Dict[str, Any]
    size: int
    data_dir: Path
    system: str
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    by_id: Dict[str, str]
    by_slug: Dict[str, str]


def render_case(case: Dict[str, Any], size: int, sandbox: Path, seed: int = SEED) -> Rendered:
    """Seed a fresh data dir for (case, size) and render the exact prompt the runtime would send."""
    from server.agents.interaction_agent import agent as agent_module
    from server.agents.interaction_agent import tools as tools_module
    from server.services.conversation import get_conversation_log

    data_dir = sandbox / f"{case['case_id']}_n{size}"
    point_stores_at(data_dir)
    roster = roster_for(case, size, seed)
    with frozen_log_clock(EVAL_CLOCK), fast_sqlite():
        id_map = seed_case(case, roster, EVAL_NOW)
    # Summarisation is off for the eval, so the transcript is the plain conversation log.
    transcript = get_conversation_log().load_transcript()
    messages = agent_module.prepare_message_with_history(case["message"], transcript,
                                                         message_type=case["message_type"])
    by_id = {agent["id"]: agent["id"] for agent in roster}
    by_id.update(id_map)
    return Rendered(case, size, data_dir, agent_module.build_system_prompt(), messages,
                    copy.deepcopy(tools_module.get_tool_schemas()), by_id,
                    {_slug(agent["name"]): agent["id"] for agent in roster})


def render_all(cases: List[Dict[str, Any]], sizes: Tuple[int, ...], sandbox: Path, seed: int,
               workers: int = 1) -> List[Rendered]:
    """Render every (case, size). Seeding is disk-bound, so large runs spread it over worker processes,
    each with its own store singletons; the data dirs stay on disk for `search_agents` to read later."""
    jobs = [(case, size) for case in cases for size in sizes]
    if workers <= 1:
        return [render_case(case, size, sandbox, seed) for case, size in jobs]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(render_case, [c for c, _ in jobs], [s for _, s in jobs],
                                 [sandbox] * len(jobs), [seed] * len(jobs)))


# ---- running (concurrent) ------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _recording_runtime_class() -> type:
    from server.agents.interaction_agent.runtime import InteractionAgentRuntime

    class RecordingRuntime(InteractionAgentRuntime):
        """The real loop and LLM call; tool calls go to a per-case recorder instead of the real handlers."""

        def __init__(self, model: str, api_key: str, tool_schemas: List[Dict[str, Any]],
                     on_tool: Callable[[str, Dict[str, Any]], Any]) -> None:
            from server.config import get_settings

            self.api_key = api_key
            self.model = model
            self.settings = get_settings()
            self.tool_schemas = tool_schemas
            self._on_tool = on_tool

        def _parse_tool_calls(self, raw_tool_calls):  # type: ignore[override]
            parsed = super()._parse_tool_calls(raw_tool_calls)
            routing = [(call.name, call.arguments) for call in parsed if call.name in ROUTING_TOOLS]
            if routing:
                raise RoutingDecided(routing)
            return parsed

        def _execute_tool(self, tool_call):  # type: ignore[override]
            return self._on_tool(tool_call.name, tool_call.arguments)

    return RecordingRuntime


def _find(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find(value, key)
            if found is not None:
                return found
    return None


def _call_cost(usage: Optional[Dict[str, Any]]) -> Optional[float]:
    """List-price cost when the provider reports one (flat plans bill 0), else the billed cost, else None."""
    if not usage:
        return None
    list_cost = _find(usage, "list_cost")
    if isinstance(list_cost, (int, float)):
        return float(list_cost)
    cost = usage.get("cost")
    return float(cost) if isinstance(cost, (int, float)) else None


@dataclass
class RunConfig:
    arm: str = "baseline"
    model: str = DEFAULT_MODEL
    sizes: Tuple[int, ...] = (5, 50, 500)
    trials: int = 1
    split: str = "report"
    limit: Optional[int] = None
    k: int = 8
    temperature: float = 0.0
    seed: int = SEED
    concurrency: int = 8
    render_workers: int = 1
    mode: str = "replay"
    cassette_dir: Path = CASSETTE_DIR
    cassette_name: Optional[str] = None
    max_live_calls: Optional[int] = None
    budget_usd: Optional[float] = None
    case_ids: Optional[List[str]] = None


async def run_one(rendered: Rendered, trial: int, config: RunConfig, api_key: str,
                  pool: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Run one (case, size, trial) through the real interaction loop and grade it."""
    from server.agents.interaction_agent import tools as tools_module
    from server.agents.interaction_agent.tools import ToolResult

    ctx = CallContext(temperature=config.temperature, seed=config.seed + trial)
    CURRENT.set(ctx)
    user_messages: List[str] = []
    offered = {schema["function"]["name"] for schema in rendered.tools}

    def on_tool(name: str, args: Dict[str, Any]) -> Any:
        if name not in offered:
            return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
        if name == "send_message_to_user":
            user_messages.append(str(args.get("message", "")))
            return ToolResult(success=True, payload={"status": "delivered"}, user_message=args.get("message"))
        if name == "search_agents":
            # Synchronous, so nothing else runs on the loop while the stores point at this case.
            point_stores_at(rendered.data_dir)
            return tools_module.handle_tool_call(name, args)
        return ToolResult(success=True, payload={"status": "recorded"})

    runtime = _recording_runtime_class()(config.model, api_key, rendered.tools, on_tool)
    calls: List[Tuple[str, Dict[str, Any]]] = []
    error: Optional[str] = None
    try:
        await runtime._run_interaction_loop(rendered.system, copy.deepcopy(rendered.messages))
    except RoutingDecided as decided:
        calls = decided.calls
    except (CassetteMiss, LiveCallLimit):
        raise
    except Exception as exc:  # the 8-iteration RuntimeError and provider errors are graded as errors
        error = f"{type(exc).__name__}: {exc}"

    resolved = [resolve_call(name, args, rendered.by_id, rendered.by_slug) for name, args in calls]
    if error:
        cell, cross = "error", False
    else:
        cell, cross = grade(rendered.case, resolved, {i: a["client_key"] for i, a in pool.items()})
    usages = [call["usage"] or {} for call in ctx.calls]
    costs = [_call_cost(u) for u in usages]
    return {
        "case_id": rendered.case["case_id"],
        "category": rendered.case["category"],
        "split": rendered.case["split"],
        "size": rendered.size,
        "trial": trial,
        "gold": rendered.case["gold"],
        "predicted": [{"tool": name, "arguments": args} for name, args in calls],
        "resolved": resolved,
        "cell": cell,
        "correct": cell in CORRECT_CELLS,
        "cross_client": cross,
        "user_messages": user_messages,
        "error": error,
        "llm_calls": len(ctx.calls),
        "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usages),
        "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usages),
        "cached_tokens": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usages),
        "latency_ms": round(sum(call["latency_ms"] for call in ctx.calls), 1),
        "cost_usd": None if all(c is None for c in costs) else sum(c or 0.0 for c in costs),
    }


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parent, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _model_slug(model: str) -> str:
    return _slug(model.replace("/", "-")) or "model"


def _arm_env(config: RunConfig) -> Dict[str, str]:
    env = dict(ARMS[config.arm])
    env.update({
        "OPENPOKE_SHORTLIST_K": str(config.k),
        "OPENPOKE_INTERACTION_MODEL": config.model,
        "OPENPOKE_LLM_TEMPERATURE": str(config.temperature),
        "OPENPOKE_LLM_SEED": str(config.seed),
        "OPENPOKE_FIXED_NOW": EVAL_NOW.isoformat(),
    })
    return env


def run(config: RunConfig, inner: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
    """Render every (case, size), run every trial concurrently, and return the results document."""
    cases = load_cases(split=config.split)
    if config.case_ids:
        wanted = set(config.case_ids)
        cases = [c for c in cases if c["case_id"] in wanted]
    if config.limit:
        cases = cases[: config.limit]
    pool = load_pool()
    api_key = os.environ.get("OPENPOKE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    if config.mode != "replay" and not api_key and inner is None:
        raise SystemExit("live and record modes need OPENPOKE_LLM_API_KEY or OPENROUTER_API_KEY")
    name = config.cassette_name or f"routing_{config.arm}_{_model_slug(config.model)}_k{config.k}"
    cassette = Cassette(name, config.mode, config.cassette_dir, inner=inner, max_live_calls=config.max_live_calls)

    saved = {key: os.environ.get(key) for key in [*_arm_env(config), "OPENPOKE_DATA_DIR"]}
    os.environ.update(_arm_env(config))
    from server.config import get_settings

    get_settings.cache_clear()
    sandbox = Path(tempfile.mkdtemp(prefix="openpoke-eval-"))
    started = time.perf_counter()
    try:
        rendered = render_all(cases, config.sizes, sandbox, config.seed, config.render_workers)
        render_s = time.perf_counter() - started
        with cassette.patch():
            records = asyncio.run(_run_all(rendered, config, api_key or "offline", pool))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        if saved["OPENPOKE_DATA_DIR"]:
            point_stores_at(Path(saved["OPENPOKE_DATA_DIR"]))
        shutil.rmtree(sandbox, ignore_errors=True)

    base_url = os.environ.get("OPENPOKE_LLM_BASE_URL") or DEFAULT_BASE_URL
    header = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "arm": config.arm,
        **{key.lower(): value for key, value in ARMS[config.arm].items()},
        "model": config.model,
        "base_url_host": urlparse(base_url).hostname,
        "commit": _git_sha(),
        "n_cases": len(cases),
        "n_records": len(records),
        "sizes": list(config.sizes),
        "trials": config.trials,
        "k": config.k,
        "temperature": config.temperature,
        "seed": config.seed,
        "split": config.split,
        "mode": config.mode,
        "cassette_hits": cassette.hits,
        "live_calls": cassette.live_calls,
        "render_seconds": round(render_s, 1),
        "cost_note": "cost_usd is the list-price equivalent when the provider reports one, else billed",
    }
    return {"header": header, "records": records}


async def _run_all(rendered: List[Rendered], config: RunConfig, api_key: str,
                   pool: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    gate = asyncio.Semaphore(config.concurrency)
    spent = {"usd": 0.0}

    async def one(item: Rendered, trial: int) -> Dict[str, Any]:
        async with gate:
            if config.budget_usd is not None and spent["usd"] > config.budget_usd:
                raise SystemExit(f"budget of ${config.budget_usd:.2f} reached (spent ${spent['usd']:.4f})")
            record = await run_one(item, trial, config, api_key, pool)
            spent["usd"] += record["cost_usd"] or 0.0
            return record

    return list(await asyncio.gather(*(one(item, t) for item in rendered for t in range(config.trials))))


def write_results(document: Dict[str, Any], directory: Path = RESULTS_DIR) -> Path:
    header = document["header"]
    stamp = header["date"][:10].replace("-", "")
    path = directory / f"{stamp}_{header['arm']}_{_model_slug(header['model'])}_k{header['k']}_{header['commit']}.json"
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return path


def _summary(records: List[Dict[str, Any]]) -> str:
    lines = []
    for size in sorted({r["size"] for r in records}):
        subset = [r for r in records if r["size"] == size]
        cells = {cell: sum(r["cell"] == cell for r in subset) for cell in CELLS}
        correct = sum(r["correct"] for r in subset)
        spend = sum(r["cost_usd"] or 0.0 for r in subset)
        shown = ", ".join(f"{cell}={count}" for cell, count in cells.items() if count)
        lines.append(f"N={size}: {correct}/{len(subset)} correct; {shown}; ${spend:.4f}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Routing eval over the golden set.")
    parser.add_argument("--arm", choices=sorted(ARMS), default="baseline")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--sizes", default="5,50,500")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--split", choices=["report", "heldout", "all"], default="report")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cases", default=None, help="comma-separated case ids")
    parser.add_argument("--k", type=int, default=8, help="shortlist size (sets OPENPOKE_SHORTLIST_K)")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--concurrency", type=int, default=8, help="LLM loops in flight")
    parser.add_argument("--render-workers", type=int, default=min(8, os.cpu_count() or 1),
                        help="processes used to seed and render the prompts")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--replay", dest="mode", action="store_const", const="replay")
    mode.add_argument("--record", dest="mode", action="store_const", const="record")
    mode.add_argument("--live", dest="mode", action="store_const", const="live")
    parser.set_defaults(mode="replay")
    parser.add_argument("--cassette-dir", type=Path, default=CASSETTE_DIR)
    parser.add_argument("--max-live-calls", type=int, default=None)
    parser.add_argument("--budget-usd", type=float, default=None)
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    config = RunConfig(
        arm=args.arm, model=args.model, sizes=tuple(int(s) for s in args.sizes.split(",")), trials=args.trials,
        split=args.split, limit=args.limit, k=args.k, temperature=args.temperature, seed=args.seed,
        concurrency=args.concurrency, render_workers=args.render_workers, mode=args.mode, cassette_dir=args.cassette_dir,
        max_live_calls=args.max_live_calls, budget_usd=args.budget_usd,
        case_ids=args.cases.split(",") if args.cases else None,
    )
    document = run(config)
    path = write_results(document, args.out_dir)
    print(_summary(document["records"]))
    header = document["header"]
    print(f"live calls {header['live_calls']}, cassette hits {header['cassette_hits']}; wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
