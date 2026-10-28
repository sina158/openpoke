"""Routing eval (plan 4.3 L2): the real interaction prompt, tool schemas and loop, with tool handling recorded.

Each case gets its own data dir, seeded through the repo's own APIs (roster, execution logs, conversation
log), and its prompt is rendered there first. The loops then run concurrently. The first assistant turn that
calls a routing tool ends the case; every routing call in that turn is graded from its arguments.

    python -m evals.run_routing --arm baseline --sizes 5,50,500 --trials 3 --replay
    python -m evals.run_routing --set v2 --arm fix_v2 --sizes 500 --trials 3 --record

`--set` picks the golden set (round one's `v1`, the fresh `v2`); `--arm` sets the server's flags for the run and
puts them back afterwards, so one process can run several arms back to back. The shared-client check (v2 c1)
only finds identifying entities on rosters of about 100 agents or more (a fixed 2% document-frequency cut-off
with no floor), so it never fires at N=5 or N=50; its zero-sends bar is asserted at N=500.
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
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlparse

from evals.cassette import CURRENT, CallContext, Cassette, CassetteMiss, LiveCallLimit, CASSETTE_DIR
from evals.generate import (GOLDEN_PATH, GOLDEN_V2_PATH, POOL_PATH, POOL_V2_PATH, SEED, SEED_V2, _slug, load_cases,
                            load_pool, roster_for)

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_MODEL = "minimax-m3"  # the committed cassettes' model, and the default off OpenRouter
# Checked against https://openrouter.ai/api/v1/models on 2026-10-28: listed, supports tools and seed.
OPENROUTER_DEFAULT_MODEL = "openai/gpt-4.1-mini"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# A run stops instead of writing a misleading file when the provider keeps failing.
MAX_ERROR_SHARE = 0.10
FIRST_CALLS_CHECKED = 5
_SHORTLIST = {"OPENPOKE_ROSTER_MODE": "shortlist", "OPENPOKE_PROMPT_VARIANT": "shortlist"}


def _profile(profile: str, disable: str = "", evidence: str = "0",
             base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """An arm under one routing profile; every v2 flag is set, so nothing leaks in from the shell."""
    return {**(base or _SHORTLIST), "OPENPOKE_ROUTING_PROFILE": profile, "OPENPOKE_V2_DISABLE": disable,
            "OPENPOKE_SHORTLIST_EVIDENCE": evidence}


ARMS: Dict[str, Dict[str, str]] = {
    "baseline": {"OPENPOKE_ROSTER_MODE": "full", "OPENPOKE_PROMPT_VARIANT": "original"},
    # Round one's arms are pinned to profile v1, so their committed cassettes replay whatever the shell says.
    # Fresh-set reports leave `fix` out; fix_v1 is its name there.
    "prompt_only": _profile("v1", base={"OPENPOKE_ROSTER_MODE": "full", "OPENPOKE_PROMPT_VARIANT": "shortlist"}),
    "fix": _profile("v1"),
    # Every agent as a card with the shortlist prompt: the cards-versus-shortlist ablation.
    "cards_full": _profile("v1", base={"OPENPOKE_ROSTER_MODE": "full_cards", "OPENPOKE_PROMPT_VARIANT": "shortlist"}),
    "fix_v1": _profile("v1"),
    "fix_v2": _profile("v2"),
    "fix_v2_no_c1": _profile("v2", disable="c1"),
    "fix_v2_no_c3": _profile("v2", disable="c3"),
    "fix_v2_no_c4": _profile("v2", disable="c4"),
    "fix_v2_evidence": _profile("v2", evidence="1"),
}
# Golden set -> (cases, pool, roster seed). The v1 set keeps --seed as its roster seed, as round one did.
SETS: Dict[str, Tuple[Path, Path, Optional[int]]] = {
    "v1": (GOLDEN_PATH, POOL_PATH, None),
    "v2": (GOLDEN_V2_PATH, POOL_V2_PATH, SEED_V2),
    "v2_paraphrase": (GOLDEN_V2_PATH.with_name("routing_v2_paraphrase.jsonl"), POOL_V2_PATH, SEED_V2),
}
# cards_full above this size only on a --cases-sample subset (a demonstration, not a sweep).
CARDS_FULL_MAX_SIZE = 50
ROUTING_TOOLS = {"send_message_to_agent", "clarify_agent_choice"}
CELLS = ("correct_reuse", "wrong_reuse", "duplicate", "over_clarify", "hijack", "correct_create",
         "correct_clarify", "wrong_guess", "partial", "no_route", "error")
# Categories whose picked agent is checked for evidence naming the case's discriminator (BARS-v2 A2).
EVIDENCE_CATEGORIES = ("near_duplicate", "should_create")
CORRECT_CELLS = {"correct_reuse", "correct_create", "correct_clarify"}
EVAL_CLOCK = datetime(2026, 10, 27, 9, 0)
# The server's archive rule (OPENPOKE_ARCHIVE_IDLE_DAYS): a gold target idle longer than this is "idle".
ARCHIVE_IDLE_DAYS = 30
# The server's "now" (OPENPOKE_FIXED_NOW) during a run: recency and card ages are rendered from it.
EVAL_NOW = EVAL_CLOCK.replace(tzinfo=timezone.utc)
_CLOCKED_MODULES = ("server.services.conversation.log", "server.services.execution.log_store",
                    "server.services.conversation.summarization.working_memory_log")
# Stores added by later branches; reset when present.
_OPTIONAL_RESETS = (("server.services.approvals", "reset_approval_store"),)


class ProviderFailure(RuntimeError):
    """Too many calls failed at the provider (auth, model, network) for the run to mean anything."""


def default_model(base_url: Optional[str]) -> str:
    """A model id that exists at the configured endpoint."""
    host = urlparse(base_url or DEFAULT_BASE_URL).hostname or ""
    return OPENROUTER_DEFAULT_MODEL if host.endswith("openrouter.ai") else DEFAULT_MODEL


def provider_error(record: Dict[str, Any]) -> bool:
    """An error from the provider rather than from the model's behaviour (the 8-iteration limit)."""
    return bool(record["error"]) and "iteration limit" not in record["error"]


class RoutingDecided(Exception):
    """Raised from inside the loop once an assistant turn has made its routing calls."""

    def __init__(self, calls: List[Tuple[str, Dict[str, Any]]], rounds: int = 1,
                 after_guard: Optional[List[Tuple[str, Dict[str, Any]]]] = None) -> None:
        super().__init__(f"{len(calls)} routing call(s)")
        self.calls = calls
        self.rounds = rounds
        self.after_guard = after_guard


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
        candidates = [str(c) for c in candidates] if isinstance(candidates, list) else []
        return {"action": "clarify", "agent_ids": [by_id[c] for c in candidates if c in by_id],
                "unknown_ids": [c for c in candidates if c not in by_id]}
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
        offered = {a for r in resolved if r["action"] == "clarify" for a in r["agent_ids"]}
        unknown = any(r.get("unknown_ids") for r in resolved if r["action"] == "clarify")
        if clarified and not reused and not created and not unknown and gold_ids <= offered:
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
    archived_ids: List[str] = field(default_factory=list)


def turn_start(now: datetime) -> set:
    """Run the server's turn-start hook (archiving idle agents, shortlist mode only); return archived pool ids."""
    from server.services.execution.registry import get_agent_registry

    try:
        lifecycle = importlib.import_module("server.services.execution.lifecycle")
        lifecycle.on_turn_start(now)
    except (ImportError, AttributeError):
        return set()
    return {record.name for record in get_agent_registry().list("archived")}


def render_case(case: Dict[str, Any], size: int, sandbox: Path, seed: int = SEED,
                pool_path: Path = POOL_PATH) -> Rendered:
    """Seed a fresh data dir for (case, size) and render the exact prompt the runtime would send."""
    from server.agents.interaction_agent import agent as agent_module
    from server.agents.interaction_agent import tools as tools_module
    from server.services.conversation import get_conversation_log

    data_dir = sandbox / f"{case['case_id']}_n{size}"
    point_stores_at(data_dir)
    roster = roster_for(case, size, seed, pool=load_pool(pool_path))
    with frozen_log_clock(EVAL_CLOCK), fast_sqlite():
        id_map = seed_case(case, roster, EVAL_NOW)
    archived = turn_start(EVAL_NOW)
    # Summarisation is off for the eval, so the transcript is the plain conversation log.
    transcript = get_conversation_log().load_transcript()
    messages = agent_module.prepare_message_with_history(case["message"], transcript,
                                                         message_type=case["message_type"])
    by_id = {agent["id"]: agent["id"] for agent in roster}
    by_id.update(id_map)
    archived_ids = [agent["id"] for agent in roster if agent["name"] in archived]
    return Rendered(case, size, data_dir, agent_module.build_system_prompt(), messages,
                    copy.deepcopy(tools_module.get_tool_schemas()), by_id,
                    {_slug(agent["name"]): agent["id"] for agent in roster}, archived_ids)


def render_all(cases: List[Dict[str, Any]], sizes: Tuple[int, ...], sandbox: Path, seed: int,
               workers: int = 1, pool_path: Path = POOL_PATH) -> List[Rendered]:
    """Render every (case, size). Seeding is disk-bound, so large runs spread it over worker processes,
    each with its own store singletons; the data dirs stay on disk for `search_agents` to read later."""
    jobs = [(case, size) for case in cases for size in sizes]
    if workers <= 1:
        return [render_case(case, size, sandbox, seed, pool_path) for case, size in jobs]
    with ProcessPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(render_case, [c for c, _ in jobs], [s for _, s in jobs],
                                 [sandbox] * len(jobs), [seed] * len(jobs), [pool_path] * len(jobs)))


# ---- running (concurrent) ------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _recording_runtime_class() -> type:
    from server.agents.interaction_agent.runtime import InteractionAgentRuntime

    class RecordingRuntime(InteractionAgentRuntime):
        """The real loop and LLM call; tool calls go to a per-case recorder instead of the real handlers."""

        def __init__(self, model: str, api_key: str, tool_schemas: List[Dict[str, Any]],
                     on_tool: Callable[[str, Dict[str, Any]], Any],
                     guard: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
                     sandbox: Optional[Path] = None) -> None:
            from server.config import get_settings

            self.api_key = api_key
            self.model = model
            self.settings = get_settings()
            self.tool_schemas = tool_schemas
            self._on_tool = on_tool
            self._guard = guard
            self._sandbox = sandbox
            self.refused: Dict[int, Any] = {}
            self.refused_again = 0  # sends refused once more in the round after the guard's answer
            self.kept: Optional[List[Tuple[str, Dict[str, Any]]]] = None

        def _parse_tool_calls(self, raw_tool_calls):  # type: ignore[override]
            parsed = super()._parse_tool_calls(raw_tool_calls)
            routing = [call for call in parsed if call.name in ROUTING_TOOLS]
            if not routing:
                return parsed
            pairs = [(call.name, call.arguments) for call in routing]
            turn_refused = self._turn_refusals(routing)
            if self.kept is not None:  # the routing turn after the guard's answer always ends the case
                # Sends the server would refuse again are not dispatched, so they are not graded.
                sent = [(c.name, c.arguments) for c in routing if id(c) not in turn_refused]
                self.refused_again = len(routing) - len(sent)
                raise RoutingDecided(self.kept + sent, rounds=2, after_guard=sent)
            if self._guard is not None:
                for call in routing:
                    refusal = self._guard(call.name, call.arguments)
                    if refusal is not None:
                        self.refused[id(call)] = refusal
            self.refused.update(turn_refused)
            if not self.refused:
                raise RoutingDecided(pairs)
            # A guard refused a call: the model gets the refusal and one more turn. Routing calls it
            # let through in the same turn count as dispatched.
            self.kept = [(c.name, c.arguments) for c in routing if id(c) not in self.refused]
            return parsed

        def _turn_refusals(self, routing):
            """The server's whole-turn check (v2 shared-client sends), run against this case's sandbox.

            In the round after a guard's answer, the sends let through in the first round count as
            already sent, as they do in the server's loop.
            """
            from server.agents.interaction_agent import tools as tools_module

            earlier = [args for name, args in self.kept or [] if name == "send_message_to_agent"]
            sends = sum(c.name == "send_message_to_agent" for c in routing)
            if self._sandbox is None or not sends or sends + len(earlier) < 2:
                return {}
            # Synchronous, so nothing else runs on the loop while the stores point at this case.
            point_stores_at(self._sandbox)
            sent = [agent_id for agent_id in map(tools_module._existing_target, earlier) if agent_id]
            return super()._check_turn_tools(routing, sent)

        def _check_turn_tools(self, tool_calls, sent=()):  # type: ignore[override]
            return {}  # applied in _parse_tool_calls, where the stores point at this case

        def _execute_tool(self, tool_call):  # type: ignore[override]
            if id(tool_call) in self.refused:
                return self.refused[id(tool_call)]
            if tool_call.name in ROUTING_TOOLS:
                return self._on_tool("__routed__", tool_call.arguments)
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
    model: Optional[str] = None
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
    case_set: str = "v1"
    cases_sample: Optional[int] = None
    sample_seed: int = SEED_V2


async def run_one(rendered: Rendered, trial: int, config: RunConfig, api_key: str,
                  pool: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Run one (case, size, trial) through the real interaction loop and grade it."""
    from server.agents.interaction_agent import tools as tools_module
    from server.agents.interaction_agent.tools import ToolResult

    ctx = CallContext(temperature=config.temperature, seed=config.seed + trial)
    CURRENT.set(ctx)
    user_messages: List[str] = []
    searches: List[bool] = []
    gold_ids = set(rendered.case["gold"]["agent_ids"])
    offered = {schema["function"]["name"] for schema in rendered.tools}

    def on_tool(name: str, args: Dict[str, Any]) -> Any:
        if name == "__routed__":  # a routing call let through in the turn where the guard refused another
            return ToolResult(success=True, payload={"status": "submitted"})
        if name not in offered:
            return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
        if name == "send_message_to_user":
            user_messages.append(str(args.get("message", "")))
            return ToolResult(success=True, payload={"status": "delivered"}, user_message=args.get("message"))
        if name == "search_agents":
            # Synchronous, so nothing else runs on the loop while the stores point at this case.
            point_stores_at(rendered.data_dir)
            result = tools_module.handle_tool_call(name, args)
            found = {rendered.by_id.get(i) for i in re.findall(r"[A-Za-z]_\d{4}", json.dumps(result.payload))}
            searches.append(bool(found & gold_ids))
            return result
        return ToolResult(success=True, payload={"status": "recorded"})

    # Upstream has no duplicate guard, so the baseline arm never runs it.
    guard = None if config.arm == "baseline" else (lambda name, args: duplicate_guard(rendered, name, args))
    sandbox = None if config.arm == "baseline" else rendered.data_dir
    runtime = _recording_runtime_class()(config.model, api_key, rendered.tools, on_tool, guard, sandbox)
    calls: List[Tuple[str, Dict[str, Any]]] = []
    after_guard: Optional[List[Tuple[str, Dict[str, Any]]]] = None
    rounds = 0
    error: Optional[str] = None
    try:
        await runtime._run_interaction_loop(rendered.system, copy.deepcopy(rendered.messages))
        calls = list(runtime.kept or [])  # the loop ended after the guard's answer with no routing call
    except RoutingDecided as decided:
        calls, rounds, after_guard = decided.calls, decided.rounds, decided.after_guard
    except (CassetteMiss, LiveCallLimit):
        raise
    except Exception as exc:  # the 8-iteration RuntimeError and provider errors are graded as errors
        error = f"{type(exc).__name__}: {exc}"

    resolved = [resolve_call(name, args, rendered.by_id, rendered.by_slug) for name, args in calls]
    guard_outcome = None
    if runtime.refused:
        suggested = {rendered.by_id.get(item["agent_id"]) for refusal in runtime.refused.values()
                     for item in refusal.payload.get("possible_duplicates", [])}
        suggested |= {rendered.by_id.get(agent_id) for refusal in runtime.refused.values()
                      for agent_id in refusal.payload.get("agent_ids", [])}
        guard_outcome = "error" if error else _guard_outcome(after_guard or [], rendered, suggested)
    client_key_of = {i: a["client_key"] for i, a in pool.items()}
    if error:
        cell, cross = "error", False
    else:
        cell, cross = grade(rendered.case, resolved, client_key_of)
    reused = {r["agent_id"] for r in resolved if r["action"] == "reuse"}
    reused_keys = [key for key in (client_key_of.get(agent_id) for agent_id in reused) if key]
    picked = [args["agent_id"] for name, args in calls
              if name == "send_message_to_agent" and isinstance(args.get("agent_id"), str)]
    usages = [call["usage"] or {} for call in ctx.calls]
    costs = [_call_cost(u) for u in usages]
    return {
        "case_id": rendered.case["case_id"],
        "category": rendered.case["category"],
        "template_id": rendered.case["template_id"],
        "split": rendered.case["split"],
        "size": rendered.size,
        "trial": trial,
        "gold": rendered.case["gold"],
        "predicted": [{"tool": name, "arguments": args} for name, args in calls],
        "resolved": resolved,
        "cell": cell,
        "correct": cell in CORRECT_CELLS,
        "cross_client": cross,
        "routing_rounds": rounds,
        "target_archived": bool(gold_ids & set(rendered.archived_ids)),
        "searches": len(searches),
        "search_found_target": any(searches),
        "guard_fired": _guard_fired(runtime.refused),
        "guard_outcome": guard_outcome,
        # Shared-client refusals over both rounds; a turn whose routing calls were all refused grades no_route.
        "shared_client_refused": _shared_client_refusals(runtime.refused) + runtime.refused_again,
        "ended_refused": _guard_fired(runtime.refused) == "shared_client" and not calls,
        "same_client_multi_send": len(reused_keys) != len(set(reused_keys)),
        # BARS-v2 B2a: the turn's sends (every round) reached agents of two or more clients, in any category.
        "multi_client_send": len(set(reused_keys)) >= 2,
        "target_idle": bool(gold_ids) and any(pool[i]["last_active_days_ago"] > ARCHIVE_IDLE_DAYS for i in gold_ids),
        "evidence_names_discriminator": evidence_check(rendered, picked),
        "user_messages": user_messages,
        "error": error,
        "llm_calls": len(ctx.calls),
        "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usages),
        "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usages),
        "cached_tokens": sum((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usages),
        "cache_reported": any("cached_tokens" in (u.get("prompt_tokens_details") or {}) for u in usages),
        "latency_ms": round(sum(call["latency_ms"] for call in ctx.calls), 1),
        "cost_usd": None if all(c is None for c in costs) else sum(c or 0.0 for c in costs),
    }


def duplicate_guard(rendered: Rendered, name: str, args: Dict[str, Any]) -> Any:
    """The server's duplicate check for a create by name, run against this case's sandbox.

    Returns the handler's own refusal (with possible_duplicates) when it would refuse, else None. Nothing is
    created: the handler is only called once the check is known to refuse.
    """
    from server.agents.interaction_agent import tools as tools_module
    from server.config import get_settings
    from server.services.execution.registry import get_agent_registry

    agent_name, instructions = args.get("agent_name"), str(args.get("instructions") or "")
    if (name != "send_message_to_agent" or args.get("agent_id") or args.get("confirm_new")
            or not isinstance(agent_name, str) or not agent_name.strip()):
        return None
    # Synchronous, so nothing else runs on the loop while the stores point at this case.
    point_stores_at(rendered.data_dir)
    settings, registry = get_settings(), get_agent_registry()
    if settings.prompt_variant != "shortlist" or registry.find_by_name(agent_name) is not None:
        return None
    cards = tools_module.cards_for(registry.list(status=None))
    if not tools_module.possible_duplicates(agent_name, instructions, cards, settings.duplicate_threshold):
        return None
    _, _, refusal = tools_module._resolve_agent(None, agent_name, False, instructions)
    return refusal


def evidence_check(rendered: Rendered, picked: List[str]) -> Optional[bool]:
    """Whether a picked agent's cited evidence names one of the case's discriminators (A2).

    The evidence is read with the server's own parser (`cards.evidence_of`). None when the check does not
    apply: another category, no discriminators, no pick, or no evidence shown.
    """
    from server.services.execution.cards import evidence_of

    terms = [t.lower() for t in rendered.case.get("discriminators") or []]
    if rendered.case["category"] not in EVIDENCE_CATEGORIES or not terms or not picked:
        return None
    prompt = "\n".join([rendered.system, *(str(m.get("content") or "") for m in rendered.messages)])
    shown = [text.lower() for text in (evidence_of(prompt, ref) for ref in picked) if text]
    if not shown:
        return None
    return any(term in text for text in shown for term in terms)


def sample_cases(cases: List[Dict[str, Any]], k: int, seed: int) -> List[Dict[str, Any]]:
    """A seeded sample of k cases, stratified by category (largest remainder), kept in file order."""
    k = min(k, len(cases))
    by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_category[case["category"]].append(case)
    quota = {c: k * len(v) / len(cases) for c, v in by_category.items()}
    counts = {c: int(q) for c, q in quota.items()}
    for c in sorted(quota, key=lambda c: (counts[c] - quota[c], c))[: k - sum(counts.values())]:
        counts[c] += 1
    rng = random.Random(seed)
    chosen = {case["case_id"] for c in sorted(by_category) for case in rng.sample(by_category[c], counts[c])}
    return [case for case in cases if case["case_id"] in chosen]


def _shared_client_refusals(refused: Dict[int, Any]) -> int:
    return sum(refusal.payload.get("reason") == "shared_client" for refusal in refused.values())


def _guard_fired(refused: Dict[int, Any]) -> Any:
    """"shared_client" when the v2 send check refused a call, else whether the duplicate guard fired."""
    if _shared_client_refusals(refused):
        return "shared_client"
    return bool(refused)


def _guard_outcome(after: List[Tuple[str, Dict[str, Any]]], rendered: Rendered, suggested: set) -> str:
    """What the model did with the guard's answer."""
    if not after:
        return "no_route"
    resolved = [resolve_call(name, args, rendered.by_id, rendered.by_slug) for name, args in after]
    if any(r["action"] == "error" for r in resolved):
        return "error"
    if any(r["action"] == "reuse" for r in resolved):
        return "reused_suggested" if any(r.get("agent_id") in suggested for r in resolved) else "reused_other"
    if any(r["action"] == "clarify" for r in resolved):
        return "clarified"
    return "confirmed_new" if any(args.get("confirm_new") for _, args in after) else "created_again"


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
    if config.model is None:  # a replay uses the model the committed cassettes were recorded with
        base_url = os.environ.get("OPENPOKE_LLM_BASE_URL")
        config.model = DEFAULT_MODEL if config.mode == "replay" else default_model(base_url)
    if config.arm == "cards_full" and max(config.sizes) > CARDS_FULL_MAX_SIZE and not config.cases_sample:
        raise ValueError(f"the cards_full arm is for N <= {CARDS_FULL_MAX_SIZE}, or a --cases-sample subset")
    golden_path, pool_path, roster_seed = SETS[config.case_set]
    if not golden_path.exists():
        raise FileNotFoundError(f"--set {config.case_set} needs {golden_path.name} in evals/golden, which does not "
                                "exist yet (generate and lock it first)")
    roster_seed = config.seed if roster_seed is None else roster_seed
    cases = load_cases(golden_path, split=config.split)
    if config.case_ids:
        wanted = set(config.case_ids)
        cases = [c for c in cases if c["case_id"] in wanted]
    if config.cases_sample:
        cases = sample_cases(cases, config.cases_sample, config.sample_seed)
    if config.limit:
        cases = cases[: config.limit]
    pool = load_pool(pool_path)
    api_key = os.environ.get("OPENPOKE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    if config.mode != "replay" and not api_key and inner is None:
        raise SystemExit("live and record modes need OPENPOKE_LLM_API_KEY or OPENROUTER_API_KEY")
    # New recordings always go to a file named for the set and arm, never to round one's files (a replay reads
    # every file in the directory, since keys are content hashes, so old cassettes still replay).
    name = config.cassette_name or f"routing_{config.case_set}_{config.arm}_{_model_slug(config.model)}_k{config.k}"
    cassette = Cassette(name, config.mode, config.cassette_dir, inner=inner, max_live_calls=config.max_live_calls)

    saved = {key: os.environ.get(key) for key in [*_arm_env(config), "OPENPOKE_DATA_DIR"]}
    os.environ.update(_arm_env(config))
    from server.config import execution_memory, get_settings, routing_profile

    get_settings.cache_clear()
    flags = {"openpoke_routing_profile": routing_profile(),
             "openpoke_v2_disable": os.environ.get("OPENPOKE_V2_DISABLE", ""),
             "openpoke_shortlist_evidence": os.environ.get("OPENPOKE_SHORTLIST_EVIDENCE", "0"),
             "openpoke_execution_memory": execution_memory()}
    sandbox = Path(tempfile.mkdtemp(prefix="openpoke-eval-"))
    started = time.perf_counter()
    try:
        rendered = render_all(cases, config.sizes, sandbox, roster_seed, config.render_workers, pool_path)
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
        **flags,
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
        "set": config.case_set,
        "roster_seed": roster_seed,
        "cases_sample": config.cases_sample,
        "sample_seed": config.sample_seed if config.cases_sample else None,
        "limit": config.limit,
        "case_ids": config.case_ids,
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
    done: List[Dict[str, Any]] = []

    async def one(item: Rendered, trial: int) -> Dict[str, Any]:
        async with gate:
            if config.budget_usd is not None and spent["usd"] > config.budget_usd:
                raise SystemExit(f"budget of ${config.budget_usd:.2f} reached (spent ${spent['usd']:.4f})")
            first = done[:FIRST_CALLS_CHECKED]
            if len(first) == FIRST_CALLS_CHECKED and all(provider_error(r) for r in first):
                raise ProviderFailure(f"the first {FIRST_CALLS_CHECKED} cases all failed: {first[0]['error']}")
            record = await run_one(item, trial, config, api_key, pool)
            spent["usd"] += record["cost_usd"] or 0.0
            done.append(record)
            return record

    records = list(await asyncio.gather(*(one(item, t) for item in rendered for t in range(config.trials))))
    failed = [r for r in records if provider_error(r)]
    if records and len(failed) / len(records) > MAX_ERROR_SHARE:
        raise ProviderFailure(f"{len(failed)} of {len(records)} cases failed at the provider: {failed[0]['error']}")
    return records


def write_results(document: Dict[str, Any], directory: Path = RESULTS_DIR) -> Path:
    header = document["header"]
    stamp = header["date"][:10].replace("-", "")
    partial = "_partial" if header.get("limit") or header.get("case_ids") else ""
    case_set = header.get("set", "v1")
    lead = "" if case_set == "v1" else f"{case_set}_"
    sample = f"_s{header['cases_sample']}" if header.get("cases_sample") else ""
    path = directory / (f"{stamp}_{lead}{header['arm']}_{_model_slug(header['model'])}_k{header['k']}"
                        f"_{header.get('split', 'report')}{sample}{partial}_{header['commit']}.json")
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
    parser.add_argument("--model", default=None,
                        help=f"default: {DEFAULT_MODEL} for a replay; live, {OPENROUTER_DEFAULT_MODEL} on OpenRouter")
    parser.add_argument("--sizes", default="5,50,500")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--split", choices=["report", "heldout", "all"], default="report")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--cases", default=None, help="comma-separated case ids")
    parser.add_argument("--set", dest="case_set", choices=sorted(SETS), default="v1",
                        help="golden set: v1 (round one) or v2 (the fresh set)")
    parser.add_argument("--cases-sample", type=int, default=None,
                        help="run a seeded sample of this many cases, stratified by category")
    parser.add_argument("--sample-seed", type=int, default=SEED_V2)
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
    parser.add_argument("--budget-usd", type=float, default=None, help="default: OPENPOKE_EVAL_BUDGET_USD")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    if args.budget_usd is None:
        from server.config import get_settings

        args.budget_usd = get_settings().eval_budget_usd

    config = RunConfig(
        arm=args.arm, model=args.model, sizes=tuple(int(s) for s in args.sizes.split(",")), trials=args.trials,
        split=args.split, limit=args.limit, k=args.k, temperature=args.temperature, seed=args.seed,
        concurrency=args.concurrency, render_workers=args.render_workers, mode=args.mode, cassette_dir=args.cassette_dir,
        max_live_calls=args.max_live_calls, budget_usd=args.budget_usd,
        case_ids=args.cases.split(",") if args.cases else None, case_set=args.case_set,
        cases_sample=args.cases_sample, sample_seed=args.sample_seed,
    )
    try:
        document = run(config)
    except ProviderFailure as exc:
        print(f"aborted, no results written: {exc}", file=sys.stderr)
        return 2
    path = write_results(document, args.out_dir)
    print(_summary(document["records"]))
    header = document["header"]
    print(f"live calls {header['live_calls']}, cassette hits {header['cassette_hits']}; wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
