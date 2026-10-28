"""Run interaction turns end to end against one sandboxed data dir, with execution agents stubbed.

The lifecycle simulation and the demo conversation share this engine. Each turn goes through the real
`InteractionAgentRuntime` (prompt, roster or shortlist, tool handlers, duplicate check, approval gate)
with `OPENPOKE_EXECUTION_STUB=1`, so a dispatched agent answers "Done: ..." without a model call but
still writes its log, its card and its last activity. The clock is `OPENPOKE_FIXED_NOW`, moved to each
message's time. LLM calls go through a cassette, so a recorded run replays offline.

Two simplifications, both so a run is deterministic and costs one model per turn:
- the agent's report is written to the conversation log as an agent message, but the interaction agent
  is not run on it (upstream would make a second model call to relay it to the user);
- the conversation history is the last `HISTORY_ENTRIES` log entries; the server's summariser is a model
  call outside the cassette, so it is off and its summary is left out.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest import mock

from evals.cassette import CURRENT, CallContext, Cassette, CassetteMiss, LiveCallLimit
from evals.run_routing import fast_sqlite, frozen_log_clock, point_stores_at

ARMS: Dict[str, Dict[str, str]] = {
    # Upstream prompt and full roster; never archives. Pinned to the round-one profile.
    "baseline": {"OPENPOKE_ROSTER_MODE": "full", "OPENPOKE_PROMPT_VARIANT": "original",
                 "OPENPOKE_ROUTING_PROFILE": "v1"},
    "fix_v1": {"OPENPOKE_ROSTER_MODE": "shortlist", "OPENPOKE_PROMPT_VARIANT": "shortlist",
               "OPENPOKE_ROUTING_PROFILE": "v1"},
    "fix_v2": {"OPENPOKE_ROSTER_MODE": "shortlist", "OPENPOKE_PROMPT_VARIANT": "shortlist",
               "OPENPOKE_ROUTING_PROFILE": "v2"},
}
HISTORY_ENTRIES = 30
# History is escaped in the prompt, so a literal <agent ...> tag is always a roster or shortlist line.
_AGENT_TAG = re.compile(r"<agent\b([^>]*)>")
_ATTR = re.compile(r'\b(id|name)="([^"]*)"')


@dataclass
class Turn:
    """What one interaction turn did, read from the registry and the messages the model saw."""

    text: str
    at: str
    message_type: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)  # {tool, arguments, status, result}
    sends: List[Dict[str, Any]] = field(default_factory=list)  # {agent_id, created, revived}
    refused_creates: int = 0
    clarified: List[str] = field(default_factory=list)
    searches: int = 0
    confirm_attempts: List[Dict[str, Any]] = field(default_factory=list)
    shown_ids: List[str] = field(default_factory=list)
    archived_at_start: List[str] = field(default_factory=list)
    archived: List[str] = field(default_factory=list)
    revived: List[str] = field(default_factory=list)
    reply: str = ""
    error: Optional[str] = None
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_chars: int = 0  # system prompt plus the first request, a model-independent size
    latency_ms: float = 0.0
    messages: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, with_messages: bool = False) -> Dict[str, Any]:
        out = dict(self.__dict__)
        if not with_messages:
            out.pop("messages")
        return out


class _Capture:
    """Wraps the cassette: keeps the turn's message list and re-raises a replay miss the runtime swallows."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self.messages: Optional[List[Dict[str, Any]]] = None
        self.system: str = ""
        self.failure: Optional[BaseException] = None

    async def __call__(self, *, model: str, messages: List[Dict[str, Any]], system: Optional[str] = None,
                       **kwargs: Any) -> Dict[str, Any]:
        if self.messages is None:
            self.messages, self.system = messages, system or ""  # the runtime appends to this same list
        # A live call waits on the network, and the stubbed agents dispatched earlier in the turn finish (and
        # log their reports) meanwhile. Finishing them first gives that same log order in every mode, so a
        # recorded run replays; without it a replay logs the reports after the turn's reply instead.
        await _finish_dispatched()
        try:
            return await self.cassette(model=model, messages=messages, system=system, **kwargs)
        except (CassetteMiss, LiveCallLimit) as exc:
            self.failure = exc
            raise


class Simulation:
    """One arm's sandbox: a data dir, the arm's environment, a cassette and a fixed clock."""

    def __init__(self, arm: str, model: str, cassette: Cassette, k: int = 8, temperature: float = 0.0,
                 seed: int = 0) -> None:
        if arm not in ARMS:
            raise ValueError(f"unknown arm {arm!r}; choose from {', '.join(ARMS)}")
        self.arm, self.model, self.cassette = arm, model, cassette
        self.k, self.temperature, self.seed = k, temperature, seed
        self.data_dir: Optional[Path] = None

    def _env(self) -> Dict[str, str]:
        env = dict(ARMS[self.arm])
        env.update({
            "OPENPOKE_EXECUTION_STUB": "1",
            "OPENPOKE_SHORTLIST_K": str(self.k),
            "OPENPOKE_INTERACTION_MODEL": self.model,
            "OPENPOKE_LLM_TEMPERATURE": str(self.temperature),
            "OPENPOKE_LLM_SEED": str(self.seed),
        })
        if not (os.environ.get("OPENPOKE_LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
            env["OPENPOKE_LLM_API_KEY"] = "offline"  # the runtime insists on a key; a replay never uses it
        return env

    @contextlib.contextmanager
    def running(self) -> Iterator["Simulation"]:
        """Set the arm's environment and a fresh data dir; restore everything on exit."""
        from server.agents.execution_agent.batch_manager import ExecutionBatchManager
        from server.agents.interaction_agent import runtime as runtime_module
        from server.config import get_settings
        from server.services.execution import lifecycle

        env = self._env()
        keys = [*env, "OPENPOKE_DATA_DIR", "OPENPOKE_FIXED_NOW"]
        saved = {key: os.environ.get(key) for key in keys}
        os.environ.update(env)
        get_settings.cache_clear()
        get_settings().conversation_summary_threshold = 0  # the summariser is a model call; see the docstring
        self.data_dir = Path(tempfile.mkdtemp(prefix="openpoke-sim-"))
        point_stores_at(self.data_dir)
        lifecycle.reset_archive_clock()
        self.capture = _Capture(self.cassette)
        try:
            with mock.patch.object(runtime_module, "request_chat_completion", self.capture), \
                    mock.patch.object(ExecutionBatchManager, "_dispatch_to_interaction_agent", _record_report):
                yield self
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            get_settings.cache_clear()
            lifecycle.reset_archive_clock()
            if saved["OPENPOKE_DATA_DIR"]:
                point_stores_at(Path(saved["OPENPOKE_DATA_DIR"]))
            shutil.rmtree(self.data_dir, ignore_errors=True)

    # ---- state ---------------------------------------------------------------------------------------------

    def statuses(self) -> Dict[str, str]:
        from server.services.execution.registry import get_agent_registry

        return {record.id: record.status for record in get_agent_registry().list(status=None)}

    def names(self) -> Dict[str, str]:
        from server.services.execution.registry import get_agent_registry

        return {record.id: record.name for record in get_agent_registry().list(status=None)}

    def seed_agent(self, name: str, first_request: str, last_response: str, last_active: datetime) -> str:
        """Add an agent as if it had run before, through the registry and log APIs; returns its id."""
        from server.services.execution import get_execution_agent_logs
        from server.services.execution.card_store import refresh_card
        from server.services.execution.registry import get_agent_registry

        os.environ["OPENPOKE_FIXED_NOW"] = last_active.isoformat()
        registry, logs = get_agent_registry(), get_execution_agent_logs()
        with frozen_log_clock(last_active.replace(tzinfo=None)), fast_sqlite():
            record, _ = registry.get_or_create(name)
            logs.record_request(name, first_request)
            logs.record_agent_response(name, last_response)
            registry.touch(record.id, now=last_active)
            refresh_card(name)
        return record.id

    def tick(self, now: datetime) -> List[str]:
        """The daily lifecycle hook at `now` (archives idle agents in shortlist mode); returns archived ids."""
        from server.services.execution import lifecycle

        os.environ["OPENPOKE_FIXED_NOW"] = now.isoformat()
        return lifecycle.on_turn_start(now)

    def execution_prompt_chars(self, agent_ids: List[str]) -> Dict[str, int]:
        """Size of each agent's execution system prompt with its history (measured, never sent)."""
        from server.agents.execution_agent.agent import ExecutionAgent
        from server.config import get_settings

        names = self.names()
        limit = get_settings().execution_history_limit
        return {agent_id: len(ExecutionAgent(names[agent_id], conversation_limit=limit)
                              .build_system_prompt_with_history()) for agent_id in agent_ids if agent_id in names}

    # ---- one turn ------------------------------------------------------------------------------------------

    def turn(self, text: str, at: datetime, message_type: str = "user") -> Turn:
        """Run one turn at `at`. `message_type` is "user" or "email" (an inbound email the watcher relays)."""
        from server.services.execution.registry import get_agent_registry

        # The runtime's own turn-start hook would archive here; running it first (it runs at most once an
        # hour) lets the snapshot see what this turn starts from, so a send to that agent counts as a revive.
        archived = self.tick(at)
        before = self.statuses()
        ctx = CallContext(temperature=self.temperature, seed=self.seed)
        self.capture.messages, self.capture.system, self.capture.failure = None, "", None
        with frozen_log_clock(at.replace(tzinfo=None)):
            result = asyncio.run(self._run(text, message_type, ctx))
        if self.capture.failure is not None:
            raise self.capture.failure
        after = self.statuses()
        messages = copy.deepcopy(self.capture.messages or [])
        turn = Turn(text=text, at=at.isoformat(), message_type=message_type, messages=messages,
                    reply=result.response, error=result.error, llm_calls=len(ctx.calls))
        usages = [call["usage"] or {} for call in ctx.calls]
        turn.prompt_tokens = sum(u.get("prompt_tokens") or 0 for u in usages)
        turn.completion_tokens = sum(u.get("completion_tokens") or 0 for u in usages)
        turn.latency_ms = round(sum(call["latency_ms"] for call in ctx.calls), 1)
        turn.archived_at_start = sorted(i for i, s in before.items() if s == "archived")
        turn.prompt_chars = len(self.capture.system) + sum(len(str(m.get("content") or "")) for m in messages[:1])
        turn.archived = archived + [i for i, s in after.items() if s == "archived" and before.get(i) == "active"]
        turn.revived = [i for i, s in after.items() if s == "active" and before.get(i) == "archived"]
        turn.shown_ids = _shown_ids(messages, get_agent_registry())
        _read_tool_results(turn, messages, before)
        return turn

    async def _run(self, text: str, message_type: str, ctx: CallContext) -> Any:
        CURRENT.set(ctx)
        runtime = _tail_runtime_class()()
        if message_type == "user":
            result = await runtime.execute(text)
        else:
            result = await runtime.handle_agent_message(text, provenance="email")
        # Let every stubbed execution agent finish (it logs, refreshes its card and touches the registry).
        await _finish_dispatched()
        return result


async def _finish_dispatched() -> None:
    """Wait until every task but the current one is done (the stubbed execution agents and their reports)."""
    while True:
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


async def _record_report(self: Any, payload: str) -> None:
    """Stands in for relaying an agent's result to the interaction agent: log it, make no model call."""
    from server.services.conversation import get_conversation_log

    get_conversation_log().record_agent_message(payload)


_TAIL_RUNTIME: Optional[type] = None


def _tail_runtime_class() -> type:
    global _TAIL_RUNTIME
    if _TAIL_RUNTIME is None:
        from server.agents.interaction_agent.runtime import InteractionAgentRuntime

        class TailRuntime(InteractionAgentRuntime):
            """The real runtime, with the conversation history cut to its last HISTORY_ENTRIES entries."""

            def _load_conversation_transcript(self) -> str:
                # Payloads are escaped, so only a line that starts an entry begins with "<".
                entries: List[str] = []
                for line in self.conversation_log.load_transcript().splitlines():
                    if line.startswith("<") or not entries:
                        entries.append(line)
                    else:
                        entries[-1] += "\n" + line
                return "\n".join(entries[-HISTORY_ENTRIES:])

        _TAIL_RUNTIME = TailRuntime
    return _TAIL_RUNTIME


def _shown_ids(messages: List[Dict[str, Any]], registry: Any) -> List[str]:
    """Ids of the agents listed in the first prompt (by id, or by name in the upstream roster)."""
    from html import unescape

    shown: List[str] = []
    for tag in _AGENT_TAG.findall(str(messages[0].get("content") or "") if messages else ""):
        attrs = dict(_ATTR.findall(tag))
        record = None if "id" in attrs else registry.find_by_name(unescape(attrs.get("name", "")))
        agent_id = attrs.get("id") or (record.id if record else None)
        if agent_id and agent_id not in shown:
            shown.append(agent_id)
    return shown


def _read_tool_results(turn: Turn, messages: List[Dict[str, Any]], before: Dict[str, str]) -> None:
    """Fill the turn's routing fields from the tool results the runtime appended to the messages."""
    for message in messages:
        if message.get("role") != "tool":
            continue
        try:
            body = json.loads(message.get("content") or "{}")
        except ValueError:
            continue
        name, args = body.get("tool"), body.get("arguments") or {}
        status, result = body.get("status"), body.get("result") or body.get("error")
        turn.tool_calls.append({"tool": name, "arguments": args, "status": status, "result": result})
        if name == "send_message_to_agent":
            if status == "success" and isinstance(result, dict) and result.get("agent_id"):
                agent_id = result["agent_id"]
                turn.sends.append({"agent_id": agent_id, "created": bool(result.get("new_agent_created")),
                                   "revived": before.get(agent_id) == "archived"})
            elif isinstance(result, dict) and result.get("status") == "possible_duplicates":
                turn.refused_creates += 1
        elif name == "clarify_agent_choice" and status == "success":
            turn.clarified.extend(str(i) for i in args.get("candidate_ids") or [])
        elif name == "search_agents":
            turn.searches += 1
        elif name == "confirm_action":
            turn.confirm_attempts.append({"action_id": args.get("action_id"), "status": status,
                                          "detail": result})
