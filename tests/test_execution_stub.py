import asyncio

from server.agents.execution_agent.runtime import ExecutionAgentRuntime
from server.config import get_settings
from server.services.execution import get_execution_agent_logs


def test_stub_mode_needs_no_key_and_writes_the_log(fresh_stores, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EXECUTION_STUB", "1")
    get_settings.cache_clear()

    result = asyncio.run(ExecutionAgentRuntime("Renewal for Example Ltd").execute("renew the policy"))

    assert result.success and result.response == "Done: renew the policy"
    entries = list(get_execution_agent_logs().iter_entries("Renewal for Example Ltd"))
    assert [(tag, text) for tag, _, text in entries] == [("agent_response", "Done: renew the policy")]


def test_execution_history_limit_reaches_the_agent(fresh_stores, monkeypatch):
    monkeypatch.setenv("OPENPOKE_EXECUTION_STUB", "1")
    monkeypatch.setenv("OPENPOKE_EXECUTION_HISTORY_LIMIT", "5")
    get_settings.cache_clear()
    assert ExecutionAgentRuntime("Any agent").agent.conversation_limit == 5
