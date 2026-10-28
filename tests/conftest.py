"""Shared test setup: every test session writes to a throwaway data dir and never needs an API key."""

import os
import tempfile

# Must happen before anything under server/ is imported: the stores resolve their paths at import time.
_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="openpoke-test-")
os.environ["OPENPOKE_DATA_DIR"] = _SESSION_DATA_DIR
os.environ.pop("OPENROUTER_API_KEY", None)
os.environ.pop("OPENPOKE_LLM_API_KEY", None)

import pytest  # noqa: E402


@pytest.fixture
def fresh_stores(tmp_path, monkeypatch):
    """Point every lazily created store at an empty data dir for one test, then reset them again."""
    from server.config import get_settings
    from server.services.approvals import reset_approval_store
    from server.services.conversation.log import reset_conversation_log
    from server.services.execution.log_store import reset_execution_agent_logs
    from server.services.execution.registry import reset_agent_registry

    resets = (reset_agent_registry, reset_execution_agent_logs, reset_conversation_log, reset_approval_store)
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path))
    for reset in resets:
        reset()
    get_settings.cache_clear()
    yield tmp_path
    monkeypatch.undo()
    for reset in resets:
        reset()
    get_settings.cache_clear()
