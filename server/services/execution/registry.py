"""SQLite registry of execution agents: a minted id per agent, with the name kept as a display label."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from ...logging_config import logger
from ...utils.clock import now_utc
from ...utils.paths import data_dir


@dataclass
class AgentRecord:
    id: str
    name: str
    slug: str
    status: str  # "active" | "archived"
    owner: str
    created_at: str  # ISO-8601 UTC
    last_active_at: str  # ISO-8601 UTC
    run_count: int
    purpose: str
    entities: list[str] = field(default_factory=list)


def slugify(name: str) -> str:
    """Convert an agent name to its filesystem-safe slug (the registry's unique key)."""
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name.strip()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "agent"


def _utc(moment: Optional[datetime] = None) -> datetime:
    """``moment`` as an aware UTC datetime (naive values are taken as UTC); defaults to now."""
    moment = moment or now_utc()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: Optional[datetime] = None) -> str:
    return _utc(moment).isoformat(timespec="seconds")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    owner TEXT NOT NULL DEFAULT 'default',
    created_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 0,
    purpose TEXT NOT NULL DEFAULT '',
    entities_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS agent_id_counter (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    last INTEGER NOT NULL
);
INSERT OR IGNORE INTO agent_id_counter (singleton, last) VALUES (1, 0);
"""

_COLUMNS = (
    "id, name, slug, status, owner, created_at, last_active_at, run_count, purpose, entities_json"
)


def _record(row: tuple) -> AgentRecord:
    return AgentRecord(
        id=row[0],
        name=row[1],
        slug=row[2],
        status=row[3],
        owner=row[4],
        created_at=row[5],
        last_active_at=row[6],
        run_count=row[7],
        purpose=row[8],
        entities=json.loads(row[9]),
    )


class AgentRegistry:
    """Agents keyed by a minted id; the slug of the name is unique, so one name maps to one agent.

    Ids are never reused, not even after ``clear()``, so a stale reference cannot land on a new agent.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=5)
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction on a fresh connection; IMMEDIATE so check-then-insert cannot race."""
        conn = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _fetch_one(self, sql: str, params: tuple) -> AgentRecord | None:
        with self._tx() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE {sql}", params).fetchone()
        return _record(row) if row else None

    def get_or_create(self, name: str) -> tuple[AgentRecord, bool]:
        """Return the agent whose slug matches ``name``, creating it if none exists."""
        slug = slugify(name)
        with self._tx() as conn:
            row = conn.execute(f"SELECT {_COLUMNS} FROM agents WHERE slug = ?", (slug,)).fetchone()
            if row:
                return _record(row), False
            return _insert(conn, name.strip() or slug), True

    def create(self, name: str) -> AgentRecord:
        """Always mint a new agent. When the name's slug is taken the name gets " (2)", " (3)", ...

        The suffix goes on the name, not just the slug, because logs and triggers are keyed by name:
        two agents with one name would share a history.
        """
        base = name.strip() or "agent"
        with self._tx() as conn:
            candidate, number = base, 1
            while conn.execute("SELECT 1 FROM agents WHERE slug = ?", (slugify(candidate),)).fetchone():
                number += 1
                candidate = f"{base} ({number})"
            return _insert(conn, candidate)

    def get(self, agent_id: str) -> AgentRecord | None:
        return self._fetch_one("id = ?", (agent_id,))

    def find_by_name(self, name: str) -> AgentRecord | None:
        return self._fetch_one("slug = ?", (slugify(name),))

    def list(self, status: str | None = "active") -> list[AgentRecord]:
        """Agents in creation order, optionally filtered by status (``None`` returns all)."""
        sql = f"SELECT {_COLUMNS} FROM agents"
        params: tuple = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status,)
        with self._tx() as conn:
            rows = conn.execute(sql + " ORDER BY rowid", params).fetchall()
        return [_record(row) for row in rows]

    def _update(self, agent_id: str, assignments: str, params: tuple) -> None:
        with self._tx() as conn:
            cursor = conn.execute(f"UPDATE agents SET {assignments} WHERE id = ?", (*params, agent_id))
            if cursor.rowcount == 0:
                raise KeyError(agent_id)

    def touch(self, agent_id: str, now: datetime | None = None) -> None:
        """Record a run: bump ``run_count`` and move ``last_active_at`` to ``now``."""
        self._update(agent_id, "last_active_at = ?, run_count = run_count + 1", (_iso(now),))

    def update_card(self, agent_id: str, purpose: str, entities: list[str]) -> None:
        self._update(agent_id, "purpose = ?, entities_json = ?", (purpose, json.dumps(entities)))

    def archive_stale(self, now: datetime, idle_days: int, protected_ids: set[str]) -> list[str]:
        """Archive active agents idle for more than ``idle_days``; never touches ``protected_ids``."""
        cutoff = _utc(now) - timedelta(days=idle_days)
        archived: list[str] = []
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, last_active_at FROM agents WHERE status = 'active' ORDER BY rowid"
            ).fetchall()
            for agent_id, last_active_at in rows:
                if agent_id in protected_ids or _utc(datetime.fromisoformat(last_active_at)) >= cutoff:
                    continue
                conn.execute("UPDATE agents SET status = 'archived' WHERE id = ?", (agent_id,))
                archived.append(agent_id)
        return archived

    def revive(self, agent_id: str, now: datetime | None = None) -> None:
        """Make an archived agent active again and restart its idle clock."""
        self._update(agent_id, "status = 'active', last_active_at = ?", (_iso(now),))

    def clear(self) -> None:
        """Delete every agent. The id counter is kept."""
        with self._tx() as conn:
            conn.execute("DELETE FROM agents")

    def migrate_from_roster_json(self, path: Path) -> int:
        """Import the names from an old ``roster.json``; returns how many agents were created.

        Idempotent: names already present (by slug) are skipped.
        """
        if not path.exists():
            return 0
        names = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(names, list):
            raise ValueError(f"{path} does not hold a list of agent names")
        created = 0
        for name in names:
            if isinstance(name, str) and name.strip():
                _, was_created = self.get_or_create(name)
                created += int(was_created)
        return created


def _insert(conn: sqlite3.Connection, name: str) -> AgentRecord:
    """Mint the next id and insert an active agent named ``name`` (its slug must be free)."""
    conn.execute("UPDATE agent_id_counter SET last = last + 1 WHERE singleton = 1")
    (number,) = conn.execute("SELECT last FROM agent_id_counter WHERE singleton = 1").fetchone()
    now = _iso()
    record = AgentRecord(
        id=f"a_{number:04d}",
        name=name,
        slug=slugify(name),
        status="active",
        owner="default",
        created_at=now,
        last_active_at=now,
        run_count=0,
        purpose="",
        entities=[],
    )
    conn.execute(
        f"INSERT INTO agents ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.id, record.name, record.slug, record.status, record.owner,
            record.created_at, record.last_active_at, record.run_count, record.purpose, "[]",
        ),
    )
    return record


_registry: AgentRegistry | None = None
_registry_lock = threading.Lock()


def get_agent_registry() -> AgentRegistry:
    """Return the registry at ``data_dir()/openpoke.db``, importing an old roster.json on first use."""
    global _registry
    with _registry_lock:
        if _registry is None:
            root = data_dir()
            registry = AgentRegistry(root / "openpoke.db")
            roster_json = root / "execution_agents" / "roster.json"
            try:
                created = registry.migrate_from_roster_json(roster_json)
            except (OSError, ValueError) as exc:
                logger.warning(f"Could not migrate {roster_json}: {exc}")
            else:
                if roster_json.exists():
                    os.replace(roster_json, roster_json.with_name("roster.json.migrated"))
                    logger.info(f"Migrated {created} agents from roster.json into the registry")
            _registry = registry
        return _registry


def reset_agent_registry() -> None:
    """Drop the singleton so the next call re-reads ``data_dir()`` (tests)."""
    global _registry
    with _registry_lock:
        _registry = None
