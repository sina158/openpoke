"""The current time, with an override so evals render byte-identical prompts on any day."""

from __future__ import annotations

import os
from datetime import datetime, timezone


def now_utc() -> datetime:
    """Aware UTC now, or ``OPENPOKE_FIXED_NOW`` (ISO-8601; naive values are UTC) when it is set."""
    fixed = os.getenv("OPENPOKE_FIXED_NOW", "").strip()
    if not fixed:
        return datetime.now(timezone.utc)
    try:
        moment = datetime.fromisoformat(fixed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"OPENPOKE_FIXED_NOW must be an ISO-8601 time, got {fixed!r}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
