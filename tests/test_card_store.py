from server.services.execution import get_agent_registry, get_execution_agent_logs
from server.services.execution.card_store import card_for, card_from_log, cards_for, refresh_card


def _seed(name, request, response):
    record, _ = get_agent_registry().get_or_create(name)
    logs = get_execution_agent_logs()
    logs.record_request(name, request)
    logs.record_agent_response(name, response)
    return record


def test_a_stored_card_is_what_the_log_gives(fresh_stores):
    record = _seed("COI for Example Ltd", "Send the COI for policy CGL-1234-56 to ops@example-ltd.test",
                   "Sent on thread 1a2b3c4d5e6f7a8b.")
    refresh_card(record.name)
    stored = get_agent_registry().get(record.id)

    assert stored.purpose and stored.entities
    assert card_for(stored) == card_from_log(stored)


def test_rendering_reads_the_stored_card_not_the_log(fresh_stores):
    record = _seed("COI for Example Ltd", "Send the COI to ops@example-ltd.test", "Done.")
    refresh_card(record.name)
    get_execution_agent_logs().record_request(record.name, "Also copy billing@example-ltd.test")

    (card,) = cards_for([get_agent_registry().get(record.id)])
    assert "billing@example-ltd.test" not in card.entities

    refresh_card(record.name)
    (card,) = cards_for([get_agent_registry().get(record.id)])
    assert "billing@example-ltd.test" in card.entities


def test_an_agent_with_nothing_stored_falls_back_to_its_log(fresh_stores):
    record = _seed("Quote for Example Ltd", "Quote property cover for ops@example-ltd.test", "Quoted.")
    (card,) = cards_for([get_agent_registry().get(record.id)])
    assert card.purpose.startswith("Quote property cover") and "ops@example-ltd.test" in card.entities
