import json
import sqlite3
import threading
from contextlib import closing
from datetime import timedelta

import pytest

from server.services import approvals
from server.services.approvals import ApprovalError, ApprovalStore, canonical_hash

REPLY = {"thread_id": "t-100", "recipient_email": "client@example.com", "message_body": "Confirmed."}


@pytest.fixture
def store(tmp_path):
    return ApprovalStore(tmp_path / "openpoke.db")


def _tamper(store, action_id, args):
    with closing(sqlite3.connect(store.db_path)) as conn, conn:
        conn.execute("UPDATE pending_actions SET args_json = ? WHERE id = ?", (json.dumps(args), action_id))


def _shift_clock(monkeypatch, hours):
    real_now = approvals._utc_now
    monkeypatch.setattr(approvals, "_utc_now", lambda: real_now() + timedelta(hours=hours))


def test_canonical_hash_ignores_key_order_but_not_content():
    assert canonical_hash("K", {"a": 1, "b": [1, 2]}) == canonical_hash("K", {"b": [1, 2], "a": 1})
    assert canonical_hash("K", {"a": 1}) != canonical_hash("K", {"a": 2})
    assert canonical_hash("K", {"a": 1}) != canonical_hash("L", {"a": 1})


def test_propose_records_args_and_hash(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY, agent_id="Email to a client")

    assert action.state == "proposed"
    assert action.args == REPLY
    assert action.args_hash == canonical_hash("GMAIL_REPLY_TO_THREAD", REPLY)
    assert action.agent_id == "Email to a client"
    assert store.get(action.id) == action
    assert [a.id for a in store.list_pending()] == [action.id]


@pytest.mark.parametrize("provenance", ["agent", "email", "automation", None])
def test_only_a_user_turn_can_approve(store, provenance):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)

    with pytest.raises(ApprovalError, match="only the user"):
        store.approve(action.id, provenance, user_text=action.confirm_code)
    assert store.get(action.id).state == "proposed"


@pytest.mark.parametrize(
    "text",
    [None, "", "what's on my calendar tomorrow?", "yes, send it", "OTHER", "X{code}9"],
)
def test_a_user_turn_without_the_code_cannot_approve(store, text):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    other = store.propose("GMAIL_FORWARD_MESSAGE", {"message_id": "m-1", "recipient_email": "a@example.com"})
    if text == "OTHER":
        text = f"yes {other.confirm_code}"
    elif text:
        text = text.format(code=action.confirm_code)

    with pytest.raises(ApprovalError, match="confirmation code"):
        store.approve(action.id, "user", user_text=text)
    assert store.get(action.id).state == "proposed"


def test_the_code_counts_in_any_case_inside_a_sentence(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)

    approved = store.approve(action.id, "user", user_text=f"ok, send it: {action.confirm_code.lower()}.")

    assert approved.state == "approved"


def test_codes_are_short_unambiguous_and_distinct_while_pending(store):
    codes = [store.propose("GMAIL_SEND_DRAFT", {"draft_id": f"r-{n}"}).confirm_code for n in range(60)]

    assert len(set(codes)) == len(codes)
    assert all(len(code) == 4 and set(code) <= set("ACDEFHJKMNPRTWXY3479") for code in codes)


def test_expire_pending_withdraws_live_actions_only(store):
    proposed = store.propose("GMAIL_SEND_DRAFT", {"draft_id": "r-1"})
    approved = store.propose("GMAIL_SEND_DRAFT", {"draft_id": "r-2"})
    sent = store.propose("GMAIL_SEND_DRAFT", {"draft_id": "r-3"})
    store.approve(approved.id, "user", user_text=approved.confirm_code)
    store.approve(sent.id, "user", user_text=sent.confirm_code)
    store.consume(sent.id)

    assert store.expire_pending() == 2
    assert [store.get(a.id).state for a in (proposed, approved, sent)] == ["expired", "expired", "executed"]
    assert store.list_pending() == []


def test_user_approval_records_the_turn(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)

    approved = store.approve(action.id, "user", turn_id="turn-1", user_text=f"yes {action.confirm_code}")

    assert approved.state == "approved"
    assert approved.approved_turn_id == "turn-1"
    assert approved.approved_at is not None


def test_consume_requires_an_approval(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)

    with pytest.raises(ApprovalError, match="not approved"):
        store.consume(action.id)
    assert store.get(action.id).state == "proposed"


def test_consume_is_single_use(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    store.approve(action.id, "user", user_text=action.confirm_code)

    executed = store.consume(action.id)

    assert executed.state == "executed"
    assert executed.executed_at is not None
    with pytest.raises(ApprovalError):
        store.consume(action.id)
    with pytest.raises(ApprovalError):
        store.approve(action.id, "user", user_text=action.confirm_code)
    assert store.list_pending() == []


def test_concurrent_consumers_execute_once(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    store.approve(action.id, "user", user_text=action.confirm_code)
    outcomes = []

    def consume():
        try:
            store.consume(action.id)
            outcomes.append("executed")
        except ApprovalError:
            outcomes.append("refused")

    threads = [threading.Thread(target=consume) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["executed"] + ["refused"] * 7


def test_args_changed_after_approval_are_refused(store):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    store.approve(action.id, "user", user_text=action.confirm_code)
    _tamper(store, action.id, {**REPLY, "bcc": ["outside@attacker.test"]})

    with pytest.raises(ApprovalError, match="changed"):
        store.consume(action.id)
    assert store.get(action.id).state == "rejected"


def test_unknown_action_is_refused(store):
    with pytest.raises(ApprovalError, match="unknown"):
        store.approve("act_000000000000", "user")
    with pytest.raises(ApprovalError, match="unknown"):
        store.consume("act_000000000000")


def test_proposal_expires_before_approval(store, monkeypatch):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    _shift_clock(monkeypatch, 25)

    assert store.list_pending() == []
    with pytest.raises(ApprovalError, match="expired"):
        store.approve(action.id, "user", user_text=action.confirm_code)
    assert store.get(action.id).state == "expired"


def test_approval_expires_before_execution(store, monkeypatch):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    store.approve(action.id, "user", user_text=action.confirm_code)
    _shift_clock(monkeypatch, 25)

    with pytest.raises(ApprovalError, match="expired"):
        store.consume(action.id)
    assert store.get(action.id).state == "expired"


def test_still_valid_inside_the_ttl(store, monkeypatch):
    action = store.propose("GMAIL_REPLY_TO_THREAD", REPLY)
    _shift_clock(monkeypatch, 23)

    store.approve(action.id, "user", user_text=action.confirm_code)
    assert store.consume(action.id).state == "executed"
