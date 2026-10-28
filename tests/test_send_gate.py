import json
import sqlite3
from contextlib import closing

import pytest

from server.agents.execution_agent.tools import get_tool_registry, get_tool_schemas, gmail
from server.agents.interaction_agent.tools import CONFIRM_ACTION_SCHEMA, handle_tool_call
from server.services.approvals import (
    ApprovalError,
    ApprovalStore,
    get_approval_store,
    reset_approval_store,
)
from server.services.execution import get_agent_registry
from server.services.execution.log_store import reset_execution_agent_logs
from server.services.provenance import turn_provenance

SEND_TOOLS = {
    "gmail_execute_draft": {"draft_id": "r-552"},
    "gmail_forward_email": {"message_id": "m-9", "recipient_email": "adjuster@example.ca"},
    "gmail_reply_to_thread": {
        "thread_id": "t-100",
        "recipient_email": "client@example.com",
        "message_body": "Your certificate is attached.",
    },
}


@pytest.fixture
def sends(monkeypatch, tmp_path):
    """Fresh approvals store, a connected fake Gmail account, and a recorder in place of Composio."""
    store = ApprovalStore(tmp_path / "openpoke.db")
    reset_approval_store(store)
    recorded = []

    def record(tool_name, composio_user_id, *, arguments=None):
        recorded.append((tool_name, arguments))
        return {"successful": True}

    monkeypatch.setattr(gmail, "execute_gmail_tool", record)
    monkeypatch.setattr(gmail, "get_active_gmail_user_id", lambda: "user-test")
    yield recorded
    reset_approval_store()


def _propose(tool="gmail_reply_to_thread"):
    result = get_tool_registry("Email to a client")[tool](**SEND_TOOLS[tool])
    assert result["status"] == "pending_approval"
    return result["action_id"]


def _confirm(action_id, provenance):
    if provenance is None:
        return handle_tool_call("confirm_action", {"action_id": action_id})
    with turn_provenance(provenance):
        return handle_tool_call("confirm_action", {"action_id": action_id})


@pytest.mark.parametrize("tool", sorted(SEND_TOOLS))
def test_send_tools_only_propose(sends, tool):
    result = get_tool_registry("Email to a client")[tool](**SEND_TOOLS[tool])

    assert result["status"] == "pending_approval"
    assert result["action_id"].startswith("act_")
    assert "summary" in result
    assert sends == []


def test_proposal_names_the_agent_by_registry_id(sends):
    record, _ = get_agent_registry().get_or_create("Email to a client")
    action_id = _propose()

    assert get_approval_store().get(action_id).agent_id == record.id


def test_proposals_are_journalled_in_the_current_log_store(sends, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path / "data"))
    reset_execution_agent_logs()
    try:
        action_id = _propose()
        journal = "".join(path.read_text(encoding="utf-8") for path in (tmp_path / "data").rglob("*.log"))
    finally:
        reset_execution_agent_logs()

    assert f"action_id={action_id}" in journal


def test_no_execute_without_a_consumed_approval(sends):
    action_id = _propose()

    with pytest.raises(ApprovalError):
        gmail.execute_approved_action(action_id)
    assert sends == []


@pytest.mark.parametrize("provenance", ["agent", "email", "automation", None])
def test_non_user_turns_cannot_confirm(sends, provenance):
    action_id = _propose()

    result = _confirm(action_id, provenance)

    assert not result.success
    assert "only the user" in result.payload["error"]
    assert sends == []


def test_user_confirmation_sends_exactly_once(sends):
    action_id = _propose()

    first = _confirm(action_id, "user")
    second = _confirm(action_id, "user")

    assert first.success and first.payload["status"] == "sent"
    assert not second.success
    assert sends == [("GMAIL_REPLY_TO_THREAD", SEND_TOOLS["gmail_reply_to_thread"])]
    with pytest.raises(ApprovalError):
        gmail.execute_approved_action(action_id)
    assert len(sends) == 1


def test_args_changed_before_confirmation_are_refused(sends):
    action_id = _propose()

    tampered = {**SEND_TOOLS["gmail_reply_to_thread"], "recipient_email": "outside@attacker.test"}
    with closing(sqlite3.connect(get_approval_store().db_path)) as conn, conn:
        conn.execute("UPDATE pending_actions SET args_json = ? WHERE id = ?", (json.dumps(tampered), action_id))

    result = _confirm(action_id, "user")

    assert not result.success
    assert "changed" in result.payload["error"]
    assert sends == []


def test_executor_is_not_a_tool():
    execution_tools = {schema["function"]["name"] for schema in get_tool_schemas()}
    assert "execute_approved_action" not in execution_tools
    assert "execute_approved_action" not in get_tool_registry("any agent")
    assert "confirm_action" not in execution_tools


def test_confirm_action_schema_is_closed():
    parameters = CONFIRM_ACTION_SCHEMA["function"]["parameters"]
    assert parameters["required"] == ["action_id"]
    assert parameters["additionalProperties"] is False
