"""Every proposed send carries a one-line summary built in code from the arguments it will send."""

import sqlite3
from contextlib import closing

from server.agents.execution_agent.tools.gmail import summarise_send
from server.services.approvals import ApprovalStore

REPLY = {
    "thread_id": "thr-7781",
    "recipient_email": "bram.keswick@example.com",
    "message_body": "Hi Bram, your renewal certificate is attached.\nBest regards.",
}


def test_summaries_are_built_from_the_arguments():
    reply = summarise_send("GMAIL_REPLY_TO_THREAD", {**REPLY, "bcc": ["collector@exfil.test"]})
    assert reply == (
        "Reply in thread thr-7781 to bram.keswick@example.com, bcc collector@exfil.test: "
        '"Hi Bram, your renewal certificate is attached."'
    )
    forward = summarise_send(
        "GMAIL_FORWARD_MESSAGE", {"message_id": "msg-0412", "recipient_email": "intake@claims.test"}
    )
    assert forward == "Forward message msg-0412 to intake@claims.test"
    draft = summarise_send("GMAIL_SEND_DRAFT", {"draft_id": "r-3390"})
    assert draft == "Send draft r-3390 (draft contents not shown - open the draft)"
    long_line = summarise_send("GMAIL_REPLY_TO_THREAD", {**REPLY, "message_body": "x" * 200})
    assert long_line.endswith('x..."') and len(long_line) < 160


def test_store_created_before_summaries_gains_the_column(tmp_path):
    path = tmp_path / "openpoke.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE pending_actions (id TEXT PRIMARY KEY, agent_id TEXT, kind TEXT NOT NULL,"
            " args_json TEXT NOT NULL, args_hash TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,"
            " approved_at TEXT, approved_turn_id TEXT, executed_at TEXT)"
        )

    action = ApprovalStore(path).propose("GMAIL_SEND_DRAFT", {"draft_id": "r-1"}, summary="Send draft r-1")

    assert action.summary == "Send draft r-1"
