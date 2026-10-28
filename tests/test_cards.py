from dataclasses import dataclass, field

import pytest

from server.services.execution.cards import (
    MAIL_ID,
    MAX_CARD_IDS,
    MAX_CARD_NAMES,
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
        ("Emailed Dana Whitfield about it", ["dana whitfield"]),
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


def test_extract_entities_can_keep_only_some_kinds():
    text = (
        "Dana Whitfield on thread 18c2f4a9b3e1d7f0, policy HO-4421-77, dana@example.com, "
        "draft_id r-1234567890123456789"
    )
    assert extract_entities(text, kinds={MAIL_ID}) == ["18c2f4a9b3e1d7f0", "r-1234567890123456789"]
    assert extract_entities(text, kinds={"name"}) == ["dana whitfield"]


def test_an_id_only_in_the_last_response_survives_a_crowded_log():
    people = ["Ada Brook", "Ben Cole", "Cy Dale", "Di Eads", "Ed Fenn", "Flo Gage"]
    contacts = people + ["Gus Hale"] * 4
    first = "contacts: " + ", ".join(f"{name} <p{i}@example.com>" for i, name in enumerate(contacts))
    log = [
        ("agent_request", first),
        ("agent_response", "Drafted the notes. " + " ".join(f"HO-{1000 + i}-11" for i in range(6))),
        ("agent_request", "Now reply to the carrier"),
        ("agent_response", "Replied to Ada Brook on thread 77a2cf9854f5c11c."),
    ]
    card = build_card(FakeRecord(), log)
    assert "77a2cf9854f5c11c" in card.entities
    assert "p0@example.com" not in card.entities  # 17 ids: the oldest mentions give way
    assert len(card.entities) == MAX_CARD_IDS + MAX_CARD_NAMES
    assert card.entities[-MAX_CARD_NAMES:] == ("ada brook", "ben cole", "cy dale", "di eads")
    assert card.entities.index("77a2cf9854f5c11c") < card.entities.index("ada brook")  # ids come first


def test_card_caps_the_record_fallback():
    record = FakeRecord(entities=[f"person{i}@example.com" for i in range(20)])
    assert len(build_card(record).entities) == MAX_CARD_IDS + MAX_CARD_NAMES


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


def test_render_card_shows_age_from_the_given_clock():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)
    card = AgentCard("a_0004", "Reminder", "active", "2026-10-24T06:00:00+00:00", "", ())
    assert 'last_active="3d ago"' in render_card(card, now=now)
    recent = AgentCard("a_0005", "Reminder", "active", "2026-10-27T04:30:00+00:00", "", ())
    assert 'last_active="4h ago"' in render_card(recent, now=now)


def _pool_card(agent_id, style):
    from evals.generate import load_pool

    agent = load_pool()[agent_id]
    log = [("agent_request", agent["first_request"]), ("agent_response", agent["last_response"])]
    return build_card(FakeRecord(id=agent_id, name=agent["name"]), log, purpose_style=style)


def test_the_sentence_style_keeps_the_city_that_tells_two_clients_apart():
    # The two Maplestone agents differ only in the city that ends the first sentence of each request.
    sarnia, barrie = _pool_card("g_0221", "sentence"), _pool_card("g_0222", "sentence")
    assert sarnia.purpose.startswith("Issue a certificate of insurance") and "Sarnia." in sarnia.purpose
    assert "Barrie." in barrie.purpose and "Barrie" not in sarnia.purpose
    for card in (_pool_card("g_0221", "clip120"), _pool_card("g_0222", "clip120")):
        assert "Sarnia" not in card.purpose and "Barrie" not in card.purpose


def test_the_longest_first_sentence_in_the_pool_survives_whole():
    from evals.generate import load_pool

    from server.services.execution.cards import _first_sentence

    length, agent_id = max((len(_first_sentence(a["first_request"])), i) for i, a in load_pool().items())
    assert length == 149
    sentence = _first_sentence(load_pool()[agent_id]["first_request"])
    assert _pool_card(agent_id, "sentence").purpose.startswith(sentence + " | ")


def test_the_sentence_style_still_clips_a_long_first_sentence():
    log = [("agent_request", "word " * 100 + "end.")]
    purpose = build_card(FakeRecord(), log, purpose_style="sentence").purpose
    assert len(purpose) <= 180 and purpose.endswith("word...")


def test_a_word_that_ends_exactly_at_the_cut_is_kept_only_when_asked():
    from server.services.execution.cards import _clip

    assert _clip("alpha beta gamma", 13) == "alpha..."
    assert _clip("alpha beta gamma", 13, keep_whole_word=True) == "alpha beta..."
    assert _clip("alpha betamax gamma", 13, keep_whole_word=True) == "alpha..."


def test_an_unknown_purpose_style_is_refused():
    with pytest.raises(ValueError):
        build_card(FakeRecord(), [("agent_request", "x")], purpose_style="clip200")


# Evidence snippets (OPENPOKE_SHORTLIST_EVIDENCE=1, profile v2).

EVIDENCE_LOG = [
    ("agent_request", "Renew the home policy for Dana Whitfield in Kingston."),
    ("tool_response", "gmail_search: Barrie office lease renewal, Barrie site visit, Barrie Barrie"),
    ("agent_response", "Sent the renewal forms; the Barrie cottage rider is still open."),
]


def test_evidence_is_the_best_matching_own_entry_and_never_raw_tool_output():
    from server.services.execution.selector import evidence

    assert evidence("the barrie cottage rider", EVIDENCE_LOG) == (2, EVIDENCE_LOG[2][1])
    assert evidence("renew dana's policy", EVIDENCE_LOG)[0] == 0
    assert evidence("barrie office lease", EVIDENCE_LOG)[0] == 2  # the tool response would score higher
    assert evidence("quarterly payroll", EVIDENCE_LOG) is None
    assert evidence("", EVIDENCE_LOG) is None


def test_evidence_is_capped_in_whole_words_around_the_match():
    from server.services.execution.selector import EVIDENCE_MAX_CHARS, evidence

    words = [f"filler{n}" for n in range(60)]
    text = " ".join(words[:40] + ["Sarnia"] + words[40:])
    _, snippet = evidence("which one is in sarnia", [("agent_request", text)])

    assert len(snippet) <= EVIDENCE_MAX_CHARS and "Sarnia" in snippet
    assert snippet.startswith("...") and snippet.endswith("...")
    assert set(snippet.strip(".").split()) <= set(text.split())


def test_evidence_renders_last_and_parses_back():
    from server.services.execution.cards import evidence_of

    card = build_card(FakeRecord(), [("agent_request", "Quote for Dana")])
    line = render_card(card, evidence=(3, 'the "Barrie" rider & cottage'))
    assert line.endswith('evidence="a_0001#3: the &quot;Barrie&quot; rider &amp; cottage" />')
    prompt = f"<active_agents>\n{render_card(card)}\n</active_agents>"

    assert evidence_of(f"<active_agents>\n{line}\n</active_agents>", "a_0001") == 'the "Barrie" rider & cottage'
    assert evidence_of(prompt, "a_0001") is None and evidence_of(prompt, "a_0002") is None
    assert render_card(card, evidence=None) == render_card(card)


@pytest.fixture
def two_agents(fresh_stores, monkeypatch):
    from server.services.execution import get_agent_registry, get_execution_agent_logs

    monkeypatch.setenv("OPENPOKE_FIXED_NOW", "2026-10-27T09:00:00Z")
    registry, logs = get_agent_registry(), get_execution_agent_logs()
    for name, request, response in [
        ("Maplestone Plumbing COI", "Issue the certificate for Maplestone Plumbing in Sarnia.", "Certificate sent."),
        ("Maplestone Heating COI", "Issue the certificate for Maplestone Heating in Barrie.", "Waiting on the holder."),
    ]:
        registry.get_or_create(name)
        logs.record_request(name, request)
        logs.record_agent_response(name, response)

    def render(evidence_flag, profile="v2", message="the Maplestone in Barrie needs its certificate"):
        from server.agents.interaction_agent.agent import prepare_message_with_history
        from server.config import get_settings

        monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", profile)
        if evidence_flag is None:
            monkeypatch.delenv("OPENPOKE_SHORTLIST_EVIDENCE", raising=False)
        else:
            monkeypatch.setenv("OPENPOKE_SHORTLIST_EVIDENCE", evidence_flag)
        get_settings.cache_clear()
        return prepare_message_with_history(message, "")[0]["content"]

    return render


def test_each_snippet_comes_from_the_cited_agents_own_log(two_agents):
    import re

    from server.services.execution import get_agent_registry, get_execution_agent_logs
    from server.services.execution.cards import evidence_of

    content = two_agents("1")
    for record in get_agent_registry().list():
        snippet = evidence_of(content, record.id)
        cite = re.search(rf'<agent id="{record.id}" [^\n]* evidence="{record.id}#(\d+): ', content)
        entries = list(get_execution_agent_logs().iter_entries(record.name))
        assert snippet and cite, record.name
        assert snippet in entries[int(cite.group(1))][2]
    sarnia = get_agent_registry().find_by_name("Maplestone Plumbing COI").id
    assert "Barrie" not in evidence_of(content, sarnia)


def test_evidence_is_matched_against_the_message_not_the_agents_name(two_agents):
    from server.services.execution import get_agent_registry
    from server.services.execution.cards import evidence_of

    heating = get_agent_registry().find_by_name("Maplestone Heating COI").id
    # The agent's name matches its request; only the message points at its response.
    content = two_agents("1", message="who is waiting on the holder")
    assert evidence_of(content, heating) == "Waiting on the holder."
    assert f'evidence="{heating}#1: ' in content
    # Both names match their own logs, so a name-keyed lookup would still quote something here.
    assert ' evidence="' not in two_agents("1", message="any news on the payroll audit")


def test_evidence_off_leaves_the_prompt_byte_identical(two_agents):
    unset = two_agents(None)
    assert two_agents("0") == unset and "evidence=" not in unset
    assert two_agents("1", profile="v1") == two_agents(None, profile="v1")
    assert two_agents("1") != unset
