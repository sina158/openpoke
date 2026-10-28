import random
import time
from datetime import datetime, timedelta, timezone

import pytest

from server.services.execution.cards import AgentCard, render_card
from server.services.execution.selector import (
    BM25,
    Scored,
    pins,
    recency_weight,
    render_shortlist,
    search,
    select,
    tokenize,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def card(agent_id, name, purpose="", entities=(), days_ago=30.0, status="active"):
    last_active = (NOW - timedelta(days=days_ago)).isoformat()
    return AgentCard(agent_id, name, status, last_active, purpose, tuple(entities))


def ids(result):
    return [item.card.agent_id for item in result]


@pytest.fixture
def roster():
    return [
        card("a_0001", "Quote for Larkspur Bakery", "Commercial property quote for the bakery", days_ago=3),
        card("a_0002", "Renewal for Dana Whitfield", "Home renewal HO-4421-77", ["dana@example.com", "ho-4421-77"], 10),
        card("a_0003", "Auto claim for Bruno Okafor", "Claim after a parking lot collision", ["bruno@example.ca"], 1),
        card("a_0004", "Certificate for Gull Harbour Marine", "COI for the marina lease", ["ops@gullharbour.test"], 40),
        card("a_0005", "Billing question for Elif Moreau", "Card declined on the monthly premium", [], 2),
        card("a_0006", "Cancellation for Hana Lindqvist", "Cancel the condo policy at the end of term", [], 60),
    ]


def test_tokenize_lowercases_and_drops_punctuation_and_stopwords():
    assert tokenize("Did Dana's HO-4421-77 renewal go out to the insured?") == [
        "dana", "s", "ho", "4421", "77", "renewal", "go", "out", "insured",
    ]


def test_bm25_prefers_rare_terms_and_scores_misses_as_zero():
    docs = [["renewal", "home"], ["renewal", "auto"], ["renewal", "claim"], ["quote"]]
    index = BM25(docs)
    assert index.score(["quote"], 3) > index.score(["renewal"], 0) > 0
    assert index.score(["quote"], 0) == 0.0
    assert index.score(["renewal", "renewal"], 0) == index.score(["renewal"], 0)


def test_bm25_normalises_for_document_length():
    index = BM25([["claim"], ["claim"] + ["filler"] * 20, ["other"]])
    assert index.score(["claim"], 0) > index.score(["claim"], 1)


def test_recency_weight_decays_and_handles_bad_timestamps():
    assert recency_weight(NOW.isoformat(), NOW) == pytest.approx(1.0)
    assert recency_weight((NOW - timedelta(days=14)).isoformat(), NOW) == pytest.approx(0.36788, abs=1e-4)
    assert recency_weight("2026-10-28T12:00:00Z", NOW) == pytest.approx(1.0)
    assert recency_weight("2026-10-28T12:00:00", NOW) == pytest.approx(1.0)  # naive means UTC
    assert recency_weight("not a date", NOW) == 0.0


def test_select_finds_the_matching_agent_first(roster):
    result = select("any update on Dana's home renewal?", roster, now=NOW)
    assert ids(result)[0] == "a_0002"
    assert all(isinstance(item, Scored) and item.pinned is None for item in result)


def test_select_is_deterministic_and_ignores_input_order(roster):
    kwargs = dict(tail_text="a_0006 said it was done", pending_ids={"a_0004"}, now=NOW, k=5)
    first = select("quote for the bakery", roster, **kwargs)
    assert select("quote for the bakery", roster, **kwargs) == first
    shuffled = list(roster)
    for seed in range(5):
        random.Random(seed).shuffle(shuffled)
        assert select("quote for the bakery", shuffled, **kwargs) == first


def test_pins_come_before_better_scores(roster):
    result = select("quote for the bakery", roster, pending_ids=["a_0006"], now=NOW)
    assert ids(result)[:2] == ["a_0006", "a_0001"]
    assert result[0].pinned == "pending"
    assert result[0].score < result[1].score


def test_pin_order_is_current_then_pending_then_latest_mention(roster):
    tail = "Earlier the Auto claim for Bruno Okafor agent replied. Then a_0005 finished."
    result = pins(tail, roster, pending_ids=["a_0004", "a_0002"], current_id="a_0006")
    assert result == [
        ("a_0006", "current"),
        ("a_0002", "pending"),
        ("a_0004", "pending"),
        ("a_0005", "recent"),
        ("a_0003", "recent"),
    ]


def test_pins_skip_unknown_ids_and_duplicates(roster):
    tail = "quote for larkspur bakery, plus the Quote for Larkspur Bakery agent"
    result = pins(tail, roster, pending_ids=["a_9999", "a_0001"], current_id="a_0001")
    assert result == [("a_0001", "current")]


def test_pins_need_a_whole_word_mention(roster):
    assert pins("see a_00011 and xquote for larkspur bakeryx", roster) == []
    assert pins("see (a_0001) now", roster) == [("a_0001", "recent")]


def test_one_word_names_are_not_pinned_by_a_passing_mention():
    cards = [card("a_0001", "Email"), card("a_0002", "Reminder for Dana")]
    assert pins("Please email the reminder for dana", cards) == [("a_0002", "recent")]


def test_max_pins_caps_pins_and_zero_disables_them(roster):
    every_id = [c.agent_id for c in roster]
    result = select("bakery", roster, pending_ids=every_id, max_pins=4, k=6, token_budget=10_000, now=NOW)
    assert [item.pinned for item in result] == ["pending"] * 4 + [None] * 2
    unpinned = select("bakery", roster, pending_ids=every_id, max_pins=0, now=NOW)
    assert all(item.pinned is None for item in unpinned)
    assert ids(unpinned)[0] == "a_0001"


def test_k_is_honoured(roster):
    assert len(select("policy", roster, k=3, token_budget=10_000, now=NOW)) == 3
    assert len(select("policy", roster, k=50, token_budget=10_000, now=NOW)) == len(roster)
    assert select("policy", roster, k=0, now=NOW) == []
    assert select("policy", [], now=NOW) == []


def test_token_budget_is_honoured(roster):
    budget = 120
    result = select("policy", roster, k=8, token_budget=budget, now=NOW)
    used = sum(len(render_card(item.card, item.pinned, NOW)) / 4 for item in result)
    assert 1 <= len(result) < len(roster)
    assert used <= budget
    assert len(select("policy", roster, token_budget=1, now=NOW)) == 1  # the top card is always kept


def test_recency_breaks_ties():
    cards = [
        card("a_0001", "Quote follow up", "Follow up on the quote", days_ago=20),
        card("a_0002", "Quote follow up", "Follow up on the quote", days_ago=2),
    ]
    assert ids(select("quote follow up", cards, now=NOW)) == ["a_0002", "a_0001"]
    assert ids(select("quote follow up", cards[::-1], scorers="bm25", now=NOW)) == ["a_0001", "a_0002"]  # then id


def test_entity_overlap_outranks_a_weak_text_match():
    cards = [card(f"a_{i:04d}", f"Quote {i}", "Send the quote to the client", days_ago=5) for i in range(1, 10)]
    target = card("a_0010", "Follow up", "Waiting on a callback", ["4165550199"], days_ago=5)
    cards.append(target)
    query = "did the quote go out? call back (416) 555-0199"
    assert ids(select(query, cards, scorers="bm25", now=NOW))[0] != "a_0010"
    assert ids(select(query, cards, scorers="bm25+entities", now=NOW))[0] == "a_0010"
    assert ids(select(query, cards, now=NOW))[0] == "a_0010"


def test_recency_only_scorer_orders_by_last_activity(roster):
    result = select("bakery", roster, scorers="recency", k=6, token_budget=10_000, now=NOW)
    assert ids(result) == ["a_0003", "a_0005", "a_0001", "a_0002", "a_0004", "a_0006"]


def test_unknown_scorer_is_rejected(roster):
    with pytest.raises(ValueError):
        select("bakery", roster, scorers="bm25+embeddings", now=NOW)


def test_search_returns_only_matches_best_first(roster):
    assert ids(search("parking collision", roster, now=NOW)) == ["a_0003"]
    assert ids(search("bruno@example.ca", roster, now=NOW))[0] == "a_0003"
    assert search("nothing like this anywhere", roster, now=NOW) == []
    two_quotes = roster + [card("a_0007", "Quote for Bruno")]
    assert sorted(ids(search("quote", two_quotes, now=NOW))) == ["a_0001", "a_0007"]
    assert len(search("quote", two_quotes, limit=1, now=NOW)) == 1


def test_render_shortlist_points_to_search_when_agents_are_hidden(roster):
    shown = select("bakery", roster, k=2, token_budget=10_000, now=NOW)
    body = render_shortlist(shown, total=len(roster)).splitlines()
    assert body[:2] == [render_card(item.card, item.pinned) for item in shown]
    assert len(body) == 3 and "4 more agents" in body[2] and "search_agents(query)" in body[2]


def test_render_shortlist_without_hidden_agents(roster):
    shown = select("bakery", roster, k=10, token_budget=10_000, now=NOW)
    assert "search_agents" not in render_shortlist(shown, total=len(roster))
    assert render_shortlist([], total=0) == "None"


def test_selecting_from_5000_cards_is_fast():
    rng = random.Random(11)
    firsts = ["Alice", "Bruno", "Dana", "Elif", "Farah", "Goran", "Hana", "Ivo", "Jun", "Kemal"]
    lasts = ["Whitfield", "Okafor", "Lindqvist", "Moreau", "Tanaka", "Ruiz", "Novak", "Berg"]
    tasks = ["renewal", "quote", "claim", "certificate", "endorsement", "cancellation", "billing"]
    cards = []
    for i in range(5000):
        first, last, task = rng.choice(firsts), rng.choice(lasts), rng.choice(tasks)
        cards.append(card(
            f"a_{i:04d}",
            f"{task.title()} for {first} {last}",
            f"Handle the {task} for {first} {last} on policy HO-{i:04d}-11 | Sent the draft, waiting on a reply.",
            [f"{first.lower()}.{last.lower()}{i}@example.com", f"{first} {last}".lower(), f"ho-{i:04d}-11"],
            days_ago=rng.uniform(0, 90),
        ))
    start = time.perf_counter()
    result = select(
        "any news on the claim for Dana Okafor? ho-0042-11",
        cards,
        tail_text="the Renewal for Hana Berg agent said it was sent",
        pending_ids=["a_0100"],
        now=NOW,
    )
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    assert ids(result)[0] == "a_0100"
    assert "a_0042" in ids(result)
