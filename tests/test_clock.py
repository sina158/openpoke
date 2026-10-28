from datetime import datetime, timezone

import pytest

from server.services.execution.registry import AgentRegistry
from server.utils.clock import now_utc


def test_fixed_now_overrides_the_clock(monkeypatch):
    monkeypatch.setenv("OPENPOKE_FIXED_NOW", "2026-10-27T09:00:00Z")
    assert now_utc() == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("OPENPOKE_FIXED_NOW", "2026-10-27T09:00")
    assert now_utc() == datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)


def test_unset_means_real_time(monkeypatch):
    monkeypatch.delenv("OPENPOKE_FIXED_NOW", raising=False)
    before = datetime.now(timezone.utc)
    assert before <= now_utc() <= datetime.now(timezone.utc)


def test_malformed_fixed_now_fails_loudly(monkeypatch):
    monkeypatch.setenv("OPENPOKE_FIXED_NOW", "yesterday")
    with pytest.raises(ValueError, match="OPENPOKE_FIXED_NOW"):
        now_utc()


def test_registry_timestamps_follow_the_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_FIXED_NOW", "2026-10-27T09:00:00Z")
    record, _ = AgentRegistry(tmp_path / "db.sqlite").get_or_create("Quote for Example Ltd")
    assert record.created_at == record.last_active_at == "2026-10-27T09:00:00+00:00"
