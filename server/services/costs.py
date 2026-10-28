"""Cost ledger: one JSON line per LLM call in ``data_dir()/costs.jsonl``."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from ..config import get_settings
from ..utils.paths import data_dir

LEDGER_FILE = "costs.jsonl"
_TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens")


class BudgetExceeded(RuntimeError):
    """Raised once recorded spend passes the budget given to ``set_budget``."""


_lock = threading.Lock()
_budget_usd: Optional[float] = None
_totals: dict[Path, float] = {}


def _ledger_path() -> Path:
    return data_dir() / LEDGER_FILE


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _count(value: Any) -> Optional[int]:
    number = _number(value)
    return None if number is None else int(number)


def _detail(usage: dict, group: str, key: str) -> Optional[int]:
    details = usage.get(group)
    return _count(details.get(key)) if isinstance(details, dict) else None


def _find_number(obj: Any, key: str) -> Optional[float]:
    """First numeric ``key`` in ``obj`` or in any dict nested inside it."""
    if not isinstance(obj, dict):
        return None
    found = _number(obj.get(key))
    if found is not None:
        return found
    for value in obj.values():
        found = _find_number(value, key)
        if found is not None:
            return found
    return None


@lru_cache(maxsize=4)
def _price_table(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _table_cost(
    model: str, prompt_tokens: Optional[int], completion_tokens: Optional[int]
) -> Optional[float]:
    """List-price cost from ``OPENPOKE_PRICES_JSON``: model -> input/output dollars per million tokens."""
    path = get_settings().llm_prices_json
    price = _price_table(path).get(model) if path else None
    if not price:
        return None
    return (
        (prompt_tokens or 0) * float(price["input_per_mtok"])
        + (completion_tokens or 0) * float(price["output_per_mtok"])
    ) / 1_000_000


def _spend(entry: dict) -> float:
    """What a call counts against the budget: the list price when known, else the billed cost."""
    for key in ("list_cost_usd", "cost_usd"):
        if entry.get(key) is not None:
            return entry[key]
    return 0.0


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _total(path: Path) -> float:
    if path not in _totals:
        _totals[path] = sum(_spend(entry) for entry in _read(path))
    return _totals[path]


def record_call(
    role: str,
    model: str,
    usage: Optional[dict],
    latency_ms: float,
    turn_id: Optional[str] = None,
) -> dict:
    """Append one call to the ledger and return the entry.

    ``cost_usd`` is what the provider billed (``usage.cost``); ``list_cost_usd`` is a list-price
    equivalent (a nested ``list_cost`` in the usage block, else the price table). Raises
    ``BudgetExceeded`` once the ledger total passes the budget.
    """
    usage = usage if isinstance(usage, dict) else {}
    prompt_tokens = _count(usage.get("prompt_tokens"))
    completion_tokens = _count(usage.get("completion_tokens"))
    list_cost = _find_number(usage, "list_cost")
    if list_cost is None:
        list_cost = _table_cost(model, prompt_tokens, completion_tokens)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "role": role,
        "model": model,
        "turn_id": turn_id,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_tokens": _detail(usage, "prompt_tokens_details", "cached_tokens"),
        "reasoning_tokens": _detail(usage, "completion_tokens_details", "reasoning_tokens"),
        "cost_usd": _number(usage.get("cost")),
        "list_cost_usd": list_cost,
        "latency_ms": round(latency_ms, 1),
    }
    path = _ledger_path()
    with _lock:
        total = _total(path) + _spend(entry)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        _totals[path] = total
    check_budget()
    return entry


def total_usd() -> float:
    """Total spend in the ledger (list price where known, else billed)."""
    with _lock:
        return _total(_ledger_path())


def summary() -> dict:
    """Call count, tokens, costs and mean latency, overall and per role."""

    def empty() -> dict:
        tokens = {field: 0 for field in _TOKEN_FIELDS}
        return {"calls": 0, **tokens, "cost_usd": 0.0, "spend_usd": 0.0, "latency_ms": 0.0}

    overall = empty()
    by_role: dict[str, dict] = {}
    for entry in _read(_ledger_path()):
        for bucket in (overall, by_role.setdefault(entry["role"], empty())):
            bucket["calls"] += 1
            for field in _TOKEN_FIELDS:
                bucket[field] += entry.get(field) or 0
            bucket["cost_usd"] += entry.get("cost_usd") or 0.0
            bucket["spend_usd"] += _spend(entry)
            bucket["latency_ms"] += entry["latency_ms"]
    for bucket in (overall, *by_role.values()):
        latency = bucket.pop("latency_ms")
        bucket["latency_ms_mean"] = latency / bucket["calls"] if bucket["calls"] else 0.0
    return {**overall, "by_role": by_role}


def set_budget(usd: Optional[float]) -> None:
    """Cap the ledger total; ``None`` removes the cap."""
    global _budget_usd
    _budget_usd = usd


def check_budget() -> None:
    """Raise ``BudgetExceeded`` if the ledger total has passed the budget."""
    if _budget_usd is None:
        return
    spent = total_usd()
    if spent > _budget_usd:
        raise BudgetExceeded(f"LLM spend ${spent:.4f} passed the ${_budget_usd:.2f} budget")
