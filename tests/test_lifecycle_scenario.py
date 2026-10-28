from collections import Counter

from evals import generate
from evals.lifecycle_scenario import CLIENTS, MATTERS, SEED, build_scenario, ideal_timeline


def test_the_same_seed_gives_the_same_month():
    assert build_scenario(SEED) == build_scenario(SEED)
    other = build_scenario(SEED + 1)
    assert [m["text"] for m in other["messages"]] != [m["text"] for m in build_scenario(SEED)["messages"]]


def test_a_shorter_run_is_a_prefix_of_the_month():
    month, half = build_scenario(), build_scenario(days=15)
    assert half["messages"] == [m for m in month["messages"] if m["day"] <= 15]
    assert half["seed_roster"] == month["seed_roster"]


def test_four_to_eight_messages_every_day_in_time_order():
    messages = build_scenario()["messages"]
    per_day = Counter(m["day"] for m in messages)
    assert sorted(per_day) == list(range(1, 31))
    assert all(4 <= count <= 8 for count in per_day.values())
    assert [m["at"] for m in messages] == sorted(m["at"] for m in messages)


def test_gold_marks_each_new_matter_once_and_ambiguity_within_one_client():
    scenario = build_scenario()
    new = [m["gold"]["matter_ids"][0] for m in scenario["messages"] if m["gold"]["kind"] == "new"]
    started_in_month = [row[0] for row in MATTERS if row[7] is None]
    assert sorted(new) == sorted(started_in_month)
    for matter_id in started_in_month:
        first = next(m for m in scenario["messages"] if matter_id in m["gold"]["matter_ids"])
        assert first["gold"]["kind"] == "new"
    ambiguous = [m for m in scenario["messages"] if m["gold"]["kind"] == "ambiguous"]
    assert len(ambiguous) == 10
    for message in ambiguous:
        candidates = message["gold"]["matter_ids"]
        assert len(candidates) >= 2
        assert {scenario["matters"][c]["client_key"] for c in candidates} == {message["client_key"]}


def test_shape_of_the_month():
    scenario = build_scenario()
    clients = {m["client_key"] for m in scenario["matters"].values()} - {"owner"}
    assert len(clients) == 12
    assert len(MATTERS) == 25
    per_client = Counter(row[1] for row in MATTERS)
    assert max(per_client.values()) == 3
    ages = [row["days_ago"] for row in scenario["seed_roster"]]
    assert min(ages) <= 5 and max(ages) >= 55


def test_the_ideal_roster_archives_and_revives_inside_the_month():
    timeline = ideal_timeline(build_scenario())
    assert len(timeline["live"]) == 30
    assert any(day > 1 for day, _ in timeline["archives"])
    assert [matter for _, matter in timeline["revives"]] == ["m06", "m15", "m09", "m23"]
    # m09 was seeded 26 days before day 1, so it is archived inside the month and comes back on day 21.
    assert (5, "m09") in timeline["archives"] and (21, "m09") in timeline["revives"]


def test_names_are_disjoint_from_the_routing_generator():
    taken = set(generate.CASE_SURNAMES) | set(generate.CONTACT_SURNAMES) | set(generate.CASE_STEMS)
    taken |= set(generate.FILLER_STEMS) | set(generate.FILLER_FIRST) | set(generate.CONTACT_FIRST)
    taken |= {name for names in generate.CASE_FIRST.values() for name in names}
    taken |= set(generate.CASE_CITIES) | set(generate.FILLER_CITIES)
    ours = {word for name, _, contact, _, _ in CLIENTS.values() for word in f"{name} {contact}".split()}
    assert not ours & taken
    assert not {row[4] for row in MATTERS} & set(generate.CARRIERS)
