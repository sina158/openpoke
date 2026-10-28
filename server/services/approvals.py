"""Single-use, hash-bound approvals for outbound actions.

An execution agent can only propose a send. The proposal is stored with a hash of its canonical
arguments. Only a user-provenance turn can approve it, and the executor consumes the approval
exactly once, re-checking the hash, before anything is sent. Consuming happens before the send,
so a retry after a timeout cannot send twice (at most once, not at least once).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from ..utils.paths import data_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    agent_id TEXT,
    kind TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT NOT NULL,
    summary TEXT,
    state TEXT NOT NULL CHECK (state IN ('proposed', 'approved', 'executed', 'rejected', 'expired')),
    created_at TEXT NOT NULL,
    approved_at TEXT,
    approved_turn_id TEXT,
    executed_at TEXT
)
"""


class ApprovalError(Exception):
    """An action cannot be approved or executed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonical_hash(kind: str, args: Dict[str, Any]) -> str:
    """Return the sha256 of the sorted-key compact JSON of ``(kind, args)``."""
    blob = json.dumps([kind, args], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class PendingAction:
    id: str
    agent_id: Optional[str]
    kind: str
    args: Dict[str, Any]
    args_hash: str
    state: str
    created_at: str
    approved_at: Optional[str] = None
    approved_turn_id: Optional[str] = None
    executed_at: Optional[str] = None
    summary: Optional[str] = None


class ApprovalStore:
    """SQLite-backed register of proposed, approved and executed outbound actions."""

    def __init__(self, db_path: Path, ttl: timedelta = timedelta(hours=24)) -> None:
        self.db_path = Path(db_path)
        self.ttl = ttl
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(pending_actions)")}
            if "summary" not in columns:  # a table created before summaries were stored
                conn.execute("ALTER TABLE pending_actions ADD COLUMN summary TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.db_path, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    def propose(
        self,
        kind: str,
        args: Dict[str, Any],
        agent_id: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> PendingAction:
        """Store a proposed action. ``summary`` is what the user is shown; build it in code from ``args``."""
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pending_actions"
                " (id, agent_id, kind, args_json, args_hash, summary, state, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?)",
                (
                    action_id,
                    agent_id,
                    kind,
                    json.dumps(args, sort_keys=True, ensure_ascii=False),
                    canonical_hash(kind, args),
                    summary,
                    _utc_now().isoformat(),
                ),
            )
        return self._require(action_id)

    def approve(
        self, action_id: str, provenance: Optional[str], turn_id: Optional[str] = None
    ) -> PendingAction:
        """Approve a proposed action. Refuses unless the approving turn came from the user."""
        if provenance != "user":
            raise ApprovalError(
                f"only the user can approve an action; this turn came from {provenance or 'an unknown source'}"
            )
        action = self._require_live(action_id)
        if action.state != "proposed":
            raise ApprovalError(f"action {action_id} is {action.state}, not awaiting approval")
        self._transition(
            action_id,
            "proposed",
            "approved",
            approved_at=_utc_now().isoformat(),
            approved_turn_id=turn_id,
        )
        return self._require(action_id)

    def consume(self, action_id: str) -> PendingAction:
        """Mark an approved action executed, once. Refuses if its stored args no longer match the hash."""
        action = self._require_live(action_id)
        if action.state != "approved":
            raise ApprovalError(f"action {action_id} is {action.state}, not approved")
        if canonical_hash(action.kind, action.args) != action.args_hash:
            self._transition(action_id, "approved", "rejected")
            raise ApprovalError(f"action {action_id} changed after it was proposed; refusing")
        self._transition(action_id, "approved", "executed", executed_at=_utc_now().isoformat())
        return self._require(action_id)

    def expire_pending(self) -> int:
        """Withdraw every proposed or approved action (the conversation they belonged to is gone)."""
        with self._connect() as conn:
            return conn.execute(
                "UPDATE pending_actions SET state = 'expired' WHERE state IN ('proposed', 'approved')"
            ).rowcount

    def get(self, action_id: str) -> Optional[PendingAction]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
        return _from_row(row) if row else None

    def list_pending(self) -> List[PendingAction]:
        """Return proposed or approved actions that have not expired, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_actions WHERE state IN ('proposed', 'approved')"
                " ORDER BY created_at, id"
            ).fetchall()
        return [action for action in map(_from_row, rows) if not self._is_expired(action)]

    def _require(self, action_id: str) -> PendingAction:
        action = self.get(action_id)
        if action is None:
            raise ApprovalError(f"unknown action {action_id}")
        return action

    def _require_live(self, action_id: str) -> PendingAction:
        action = self._require(action_id)
        if action.state in ("proposed", "approved") and self._is_expired(action):
            self._transition(action_id, action.state, "expired")
            raise ApprovalError(f"action {action_id} expired; ask the agent to propose it again")
        return action

    def _is_expired(self, action: PendingAction) -> bool:
        return _utc_now() - datetime.fromisoformat(action.created_at) > self.ttl

    def _transition(self, action_id: str, from_state: str, to_state: str, **fields: Optional[str]) -> None:
        # The state guard in the WHERE clause makes each transition atomic: of two racing callers,
        # only one sees rowcount 1.
        assignments = ", ".join(["state = ?", *(f"{name} = ?" for name in fields)])
        with self._connect() as conn:
            updated = conn.execute(
                f"UPDATE pending_actions SET {assignments} WHERE id = ? AND state = ?",
                (to_state, *fields.values(), action_id, from_state),
            ).rowcount
        if updated != 1:
            raise ApprovalError(f"action {action_id} is no longer {from_state}")


def _from_row(row: sqlite3.Row) -> PendingAction:
    return PendingAction(
        id=row["id"],
        agent_id=row["agent_id"],
        kind=row["kind"],
        args=json.loads(row["args_json"]),
        args_hash=row["args_hash"],
        state=row["state"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
        approved_turn_id=row["approved_turn_id"],
        executed_at=row["executed_at"],
        summary=row["summary"],
    )


_store: Optional[ApprovalStore] = None


def get_approval_store() -> ApprovalStore:
    """Return the shared store in ``data_dir() / "openpoke.db"``, created on first use."""
    global _store
    if _store is None:
        _store = ApprovalStore(data_dir() / "openpoke.db")
    return _store


def reset_approval_store(store: Optional[ApprovalStore] = None) -> None:
    """Drop the shared store, or replace it (tests and evals)."""
    global _store
    _store = store


__all__ = [
    "ApprovalError",
    "ApprovalStore",
    "PendingAction",
    "canonical_hash",
    "get_approval_store",
    "reset_approval_store",
]
