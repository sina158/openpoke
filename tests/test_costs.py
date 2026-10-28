import asyncio
import json

import httpx
import pytest

from server.config import get_settings
from server.openrouter_client import client as llm_client
from server.services import costs

OPENROUTER_USAGE = {
    "prompt_tokens": 1000,
    "completion_tokens": 200,
    "cost": 0.0042,
    "prompt_tokens_details": {"cached_tokens": 600},
}
FLAT_PLAN_USAGE = {
    "prompt_tokens": 467,
    "completion_tokens": 96,
    "prompt_tokens_details": {"cached_tokens": 128},
    "completion_tokens_details": {"reasoning_tokens": 46},
    "cost": 0,
    "gateway": {"billing_source": "subscription", "list_cost": 0.00022458},
}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("OPENPOKE_PRICES_JSON", raising=False)
    get_settings.cache_clear()
    yield tmp_path / costs.LEDGER_FILE
    costs.set_budget(None)
    get_settings.cache_clear()


def test_billed_cost_and_tokens_are_recorded(ledger):
    entry = costs.record_call("interaction", "vendor/model-a", OPENROUTER_USAGE, 812.34, turn_id="t1")

    assert entry["role"] == "interaction" and entry["turn_id"] == "t1"
    assert (entry["prompt_tokens"], entry["completion_tokens"], entry["cached_tokens"]) == (1000, 200, 600)
    assert entry["reasoning_tokens"] is None
    assert entry["cost_usd"] == 0.0042 and entry["list_cost_usd"] is None
    assert entry["latency_ms"] == 812.3
    assert [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()] == [entry]
    assert costs.total_usd() == pytest.approx(0.0042)


def test_list_cost_counts_against_the_budget_on_a_flat_plan(ledger):
    entry = costs.record_call("execution", "vendor/model-b", FLAT_PLAN_USAGE, 100.0)

    assert entry["cost_usd"] == 0.0
    assert entry["list_cost_usd"] == pytest.approx(0.00022458)
    assert (entry["cached_tokens"], entry["reasoning_tokens"]) == (128, 46)
    assert costs.total_usd() == pytest.approx(0.00022458)


def test_missing_usage_records_nulls(ledger):
    entry = costs.record_call("summarizer", "vendor/model-a", None, 5.0)
    assert entry["prompt_tokens"] is None and entry["cost_usd"] is None and entry["list_cost_usd"] is None
    assert costs.total_usd() == 0.0


def test_price_table_fills_list_cost_when_the_provider_reports_none(ledger, tmp_path, monkeypatch):
    prices = tmp_path / "prices.json"
    table = {"vendor/model-a": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}}
    prices.write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setenv("OPENPOKE_PRICES_JSON", str(prices))
    get_settings.cache_clear()

    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}
    priced = costs.record_call("search", "vendor/model-a", usage, 1.0)
    unpriced = costs.record_call("search", "vendor/unknown", {"prompt_tokens": 10}, 1.0)

    assert priced["list_cost_usd"] == pytest.approx(4.5) and priced["cost_usd"] is None
    assert unpriced["list_cost_usd"] is None
    assert costs.total_usd() == pytest.approx(4.5)


def test_budget_raises_once_passed_and_blocks_further_calls(ledger):
    costs.set_budget(0.01)
    costs.record_call("interaction", "m", {"cost": 0.006}, 1.0)
    costs.check_budget()

    with pytest.raises(costs.BudgetExceeded):
        costs.record_call("interaction", "m", {"cost": 0.006}, 1.0)
    with pytest.raises(costs.BudgetExceeded):
        costs.check_budget()
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2

    costs.set_budget(None)
    costs.check_budget()


def test_total_survives_a_restart(ledger):
    costs.record_call("interaction", "m", {"cost": 0.25}, 1.0)
    costs._totals.clear()
    assert costs.total_usd() == pytest.approx(0.25)


def test_summary_groups_by_role(ledger):
    costs.record_call("interaction", "m", OPENROUTER_USAGE, 100.0)
    costs.record_call("interaction", "m", FLAT_PLAN_USAGE, 300.0)
    costs.record_call("classifier", "m", None, 50.0)

    report = costs.summary()

    assert report["calls"] == 3
    assert report["prompt_tokens"] == 1467 and report["cached_tokens"] == 728
    assert report["spend_usd"] == pytest.approx(0.0042 + 0.00022458)
    assert report["by_role"]["interaction"]["calls"] == 2
    assert report["by_role"]["interaction"]["latency_ms_mean"] == 200.0
    assert report["by_role"]["classifier"]["cost_usd"] == 0.0


def _mock_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        llm_client.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler))
    )


def _call():
    return asyncio.run(
        llm_client.request_chat_completion(
            model="vendor/model-a",
            messages=[{"role": "user", "content": "hi"}],
            api_key="test-key",
            role="classifier",
        )
    )


def test_client_records_each_call(ledger, monkeypatch):
    body = {"choices": [{"message": {"content": "ok"}}], "usage": OPENROUTER_USAGE}
    _mock_transport(monkeypatch, lambda request: httpx.Response(200, json=body))

    assert _call() == body
    (entry,) = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert entry["role"] == "classifier" and entry["model"] == "vendor/model-a"
    assert entry["cost_usd"] == 0.0042 and entry["latency_ms"] >= 0


def test_client_refuses_to_call_once_over_budget(ledger, monkeypatch):
    sent = []
    _mock_transport(monkeypatch, lambda request: sent.append(request) or httpx.Response(200, json={}))
    costs.record_call("interaction", "m", {"cost": 1.0}, 1.0)
    costs.set_budget(0.5)

    with pytest.raises(costs.BudgetExceeded):
        _call()
    assert sent == []
