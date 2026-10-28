"""Record and replay LLM calls so the routing eval reruns offline for free.

A cassette stands in for `request_chat_completion` where the interaction runtime imports it. Calls are keyed
by sha256 of (model, messages including the system prompt, tools, temperature, seed). Only the response body
and the measured latency are stored; request headers and keys never are.

Modes: `live` calls the model and stores nothing; `record` replays what it has and records the rest;
`replay` never touches the network and raises `CassetteMiss` on an unrecorded call.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional

CASSETTE_DIR = Path(__file__).resolve().parent / "cassettes"
MODES = ("live", "record", "replay")
RUNTIME_TARGET = "server.agents.interaction_agent.runtime.request_chat_completion"

LLMCall = Callable[..., Awaitable[Dict[str, Any]]]


class CassetteMiss(Exception):
    """A replay asked for a call that was never recorded."""


class LiveCallLimit(Exception):
    """The run tried to make more live calls than it was allowed."""


@dataclass
class CallContext:
    """Per-case sampling settings and the calls made so far; set once per case task."""

    temperature: Optional[float] = None
    seed: Optional[int] = None
    calls: List[Dict[str, Any]] = field(default_factory=list)


CURRENT: ContextVar[Optional[CallContext]] = ContextVar("eval_call_context", default=None)


def call_key(model: str, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]],
             temperature: Optional[float], seed: Optional[int]) -> str:
    blob = json.dumps(
        {"model": model, "messages": messages, "tools": tools or [], "temperature": temperature, "seed": seed},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _slim(response: Dict[str, Any]) -> Dict[str, Any]:
    """Keep what the runtime and the report read: the message, finish reason, usage and model."""
    choices = [
        {"message": choice.get("message"), "finish_reason": choice.get("finish_reason")}
        for choice in response.get("choices") or []
    ]
    return {"model": response.get("model"), "choices": choices, "usage": response.get("usage")}


def _real_client() -> LLMCall:
    from server.openrouter_client import client

    return client.request_chat_completion


class Cassette:
    """Drop-in async replacement for `request_chat_completion`."""

    def __init__(self, name: str, mode: str = "replay", directory: Path = CASSETTE_DIR,
                 inner: Optional[LLMCall] = None, max_live_calls: Optional[int] = None) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.name = name
        self.mode = mode
        self.directory = Path(directory)
        self.path = self.directory / f"{name}.jsonl"
        self._inner = inner
        self.max_live_calls = max_live_calls
        self.live_calls = 0
        self.hits = 0
        self.entries: Dict[str, Dict[str, Any]] = {}
        if mode != "live":
            self._load()

    def _load(self) -> None:
        # Keys are content hashes, so every file in the directory can serve every run.
        for path in sorted(self.directory.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self.entries[entry["key"]] = entry

    async def __call__(self, *, model: str, messages: List[Dict[str, Any]], system: Optional[str] = None,
                       tools: Optional[List[Dict[str, Any]]] = None, **kwargs: Any) -> Dict[str, Any]:
        ctx = CURRENT.get() or CallContext()
        temperature = kwargs.get("temperature", ctx.temperature)
        seed = kwargs.get("seed", ctx.seed)
        full = ([{"role": "system", "content": system}] if system else []) + list(messages)
        key = call_key(model, full, tools, temperature, seed)

        if self.mode != "live" and key in self.entries:
            entry = self.entries[key]
            self.hits += 1
            ctx.calls.append({"usage": entry["response"].get("usage"), "latency_ms": entry["latency_ms"],
                              "replayed": True})
            return copy.deepcopy(entry["response"])
        if self.mode == "replay":
            raise CassetteMiss(f"no recorded response for key {key[:16]} (model {model}) in {self.directory}")

        if self.max_live_calls is not None and self.live_calls >= self.max_live_calls:
            raise LiveCallLimit(f"live call limit of {self.max_live_calls} reached")
        self.live_calls += 1
        inner = self._inner or _real_client()
        accepted = inspect.signature(inner).parameters
        call_kwargs = dict(kwargs)
        for name, value in (("temperature", temperature), ("seed", seed)):
            if name in accepted and value is not None:
                call_kwargs.setdefault(name, value)
        started = time.perf_counter()
        response = await inner(model=model, messages=messages, system=system, tools=tools, **call_kwargs)
        latency_ms = (time.perf_counter() - started) * 1000
        slim = _slim(response)
        ctx.calls.append({"usage": slim.get("usage"), "latency_ms": latency_ms, "replayed": False})
        if self.mode == "record":
            entry = {"key": key, "model": model, "temperature": temperature, "seed": seed,
                     "latency_ms": round(latency_ms, 1), "response": slim}
            self.entries[key] = entry
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return copy.deepcopy(slim)

    @contextlib.contextmanager
    def patch(self, target: str = RUNTIME_TARGET) -> Iterator["Cassette"]:
        """Install this cassette where the interaction runtime looks up `request_chat_completion`."""
        module_name, attr = target.rsplit(".", 1)
        module = __import__(module_name, fromlist=[attr])
        original = getattr(module, attr)
        setattr(module, attr, self)
        try:
            yield self
        finally:
            setattr(module, attr, original)
