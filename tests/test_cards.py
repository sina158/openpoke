from dataclasses import dataclass, field

import pytest

from server.services.execution.cards import (
    MAX_CARD_ENTITIES,
    PURPOSE_MAX_CHARS,
    AgentCard,
    build_card,
    card_text,
    extract_entities,
    render_card,
)


@dataclass
class FakeRecord:
    id: str = "a_0001"
    name: str = "Email to Alice Chen"
    status: str = "active"
    last_active_at: str = "2026-10-20T09:15:00+00:00"
    purpose: str = ""
    entities: list[str] = field(default_factory=list)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Ping Ops@SmithPlumbing.Example.ca today", ["ops@smithplumbing.example.ca"]),
        ("Renewal for HO-4421-77, POL123456 and CGL-2024-0091", ["ho-4421-77", "pol123456", "cgl-2024-0091"]),
        ('{"thread_id": "18C2F4A9B3E1D7F0", "draft_id": "r-1234567890123456789"}',
         ["18c2f4a9b3e1d7f0", "r-1234567890123456789"]),
        ("Reply on thread t_91 with the draft", ["t_91"]),
        ("Ask Dana Whitfield at Larkspur Brokers", ["dana whitfield", "larkspur brokers"]),
        ("Send Alice Chen the quote", ["alice chen"]),
        ("Call +1 (416) 555-0199 or 416.555.0199", ["4165550199"]),
    ],
)
def test_extracts_and_normalises_entities(text, expected):
    assert extract_entities(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Alice called about it",  # a single capitalised word is not a name
        "Please Send. Monday Morning. Thanks Alice.",  # Title Case that is not a name
        "COVID-19 delayed the W-2 and the MP3",  # short codes are not policy numbers
        "Meeting 2026-10-28 14:30, total $1,250, ext 555-0199",  # dates, money, short numbers
        "the thread id about the renewal",  # a label with no id after it
        "",
    ],
)
def test_ignores_things_that_are_not_entities(text):
    assert extract_entities(text) == []


def test_entities_are_deduplicated_in_order_of_first_appearance():
    text = "HO-4421-77 for alice@example.com, then ALICE@example.com and ho-4421-77 again"
    assert extract_entities(text) == ["ho-4421-77", "alice@example.com"]


def test_email_local_part_is_not_read_as_a_policy_number():
    assert extract_entities("pol123456@example.com") == ["pol123456@example.com"]


def test_card_purpose_is_first_request_and_head_of_last_response():
    log = [
        ("agent_request", "Email   alice@example.com\nabout the HO-4421-77 renewal"),
        ("agent_response", "Drafted the first email."),
        ("agent_request", "Now follow up with her"),
        ("agent_response", "Sent the follow-up; waiting on her reply."),
    ]
    card = build_card(FakeRecord(), log)
    assert card.purpose == (
        "Email alice@example.com about the HO-4421-77 renewal | Sent the follow-up; waiting on her reply."
    )
    assert card.entities == ("alice@example.com", "ho-4421-77")
    assert (card.agent_id, card.name, card.status) == ("a_0001", "Email to Alice Chen", "active")


def test_card_purpose_is_capped():
    log = [("agent_request", "word " * 200), ("agent_response", "result " * 200)]
    assert len(build_card(FakeRecord(), log).purpose) <= PURPOSE_MAX_CHARS


def test_card_ignores_entities_inside_raw_tool_responses():
    log = [
        ("agent_request", "Find the latest email from dana@example.com"),
        ("tool_response", "gmail_search: from bulk1@example.test, bulk2@example.test, HO-9999-11"),
    ]
    assert build_card(FakeRecord(), log).entities == ("dana@example.com",)


def test_card_caps_entities():
    emails = " ".join(f"person{i}@example.com" for i in range(MAX_CARD_ENTITIES + 5))
    card = build_card(FakeRecord(), [("agent_request", emails)])
    assert len(card.entities) == MAX_CARD_ENTITIES
    assert card.entities[0] == "person0@example.com"


def test_card_falls_back_to_the_record_without_a_log():
    record = FakeRecord(
        purpose="Quote  for Smith Plumbing",
        entities=["Ops@SmithPlumbing.example.ca", "ops@smithplumbing.example.ca"],
    )
    card = build_card(record)
    assert card.purpose == "Quote for Smith Plumbing"
    assert card.entities == ("ops@smithplumbing.example.ca",)


def test_build_card_is_deterministic():
    log = [("agent_request", "Quote for Dana Whitfield, dana@example.ca, POL123456")]
    assert build_card(FakeRecord(), log) == build_card(FakeRecord(), list(log))


def test_card_text_holds_name_purpose_and_entities():
    card = AgentCard("a_0007", "Renewal agent", "active", "2026-10-01", "Renew the home policy", ("ho-4421-77",))
    assert card_text(card) == "Renewal agent Renew the home policy ho-4421-77"


def test_render_card_escapes_attribute_values_on_one_line():
    card = AgentCard(
        agent_id="a_0003",
        name='Reply to "Bob" <boss>',
        status="active",
        last_active_at="2026-10-27T08:30:12+00:00",
        purpose="Answer\nthe <script> & 'quote'",
        entities=("bob@example.com",),
    )
    line = render_card(card, pinned="recent")
    assert "\n" not in line
    assert line == (
        '<agent id="a_0003" name="Reply to &quot;Bob&quot; &lt;boss&gt;" status="active" '
        'last_active="2026-10-27T08:30" pinned="recent" '
        'purpose="Answer the &lt;script&gt; &amp; &#x27;quote&#x27;" entities="bob@example.com" />'
    )


def test_render_card_omits_empty_optional_attributes():
    card = AgentCard("a_0004", "Reminder", "active", "2026-10-27", "", ())
    assert render_card(card) == '<agent id="a_0004" name="Reminder" status="active" last_active="2026-10-27" />'
