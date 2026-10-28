import asyncio
import json

import httpx
import pytest

from server.config import DEFAULT_MODEL, get_settings
from server.openrouter_client import client as llm_client

_LLM_ENV = [
    "OPENPOKE_INTERACTION_MODEL",
    "OPENPOKE_EXECUTION_MODEL",
    "OPENPOKE_SEARCH_MODEL",
    "OPENPOKE_SUMMARIZER_MODEL",
    "OPENPOKE_CLASSIFIER_MODEL",
    "OPENPOKE_LLM_BASE_URL",
    "OPENPOKE_LLM_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENPOKE_LLM_TEMPERATURE",
    "OPENPOKE_LLM_SEED",
    "OPENPOKE_LLM_PROVIDER",
    "OPENPOKE_LLM_HEADERS",
    "OPENPOKE_ROSTER_MODE",
    "OPENPOKE_PROMPT_VARIANT",
    "OPENPOKE_SHORTLIST_K",
]


@pytest.fixture
def env(monkeypatch):
    """Clean LLM env and a settings cache that re-reads it."""
    for name in _LLM_ENV:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_defaults_are_unchanged(env):
    settings = get_settings()
    assert settings.interaction_agent_model == DEFAULT_MODEL == "anthropic/claude-sonnet-4"
    assert settings.email_classifier_model == DEFAULT_MODEL
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.llm_temperature is None and settings.llm_seed is None
    assert settings.llm_provider_order == [] and settings.llm_headers == {}
    assert (settings.roster_mode, settings.prompt_variant) == ("shortlist", "shortlist")
    assert settings.shortlist_k == 8
    assert (settings.duplicate_threshold, settings.archive_idle_days) == (0.85, 30)
    assert settings.execution_history_limit is None and settings.eval_budget_usd == 40.0


def test_env_overrides_are_reread_after_cache_clear(env):
    env.setenv("OPENPOKE_INTERACTION_MODEL", "vendor/router-small")
    env.setenv("OPENPOKE_SEARCH_MODEL", "vendor/search-small")
    env.setenv("OPENPOKE_LLM_TEMPERATURE", "0")
    env.setenv("OPENPOKE_LLM_SEED", "7")
    env.setenv("OPENPOKE_LLM_PROVIDER", "alpha, beta")
    env.setenv("OPENPOKE_ROSTER_MODE", "FULL")
    env.setenv("OPENPOKE_LLM_API_KEY", "key-a")
    env.setenv("OPENROUTER_API_KEY", "key-b")
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.interaction_agent_model == "vendor/router-small"
    assert settings.execution_agent_search_model == "vendor/search-small"
    assert settings.execution_agent_model == DEFAULT_MODEL
    assert settings.llm_temperature == 0.0 and settings.llm_seed == 7
    assert settings.llm_provider_order == ["alpha", "beta"]
    assert settings.roster_mode == "full"
    assert settings.openrouter_api_key == "key-a"

    env.delenv("OPENPOKE_LLM_API_KEY")
    get_settings.cache_clear()
    assert get_settings().openrouter_api_key == "key-b"


@pytest.mark.parametrize(
    "name, value",
    [
        ("OPENPOKE_ROSTER_MODE", "everything"),
        ("OPENPOKE_LLM_SEED", "seven"),
        ("OPENPOKE_LLM_HEADERS", "not json"),
        ("OPENPOKE_LLM_HEADERS", '{"X-Count": 3}'),
    ],
)
def test_malformed_values_fail_loudly(env, name, value):
    env.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        get_settings()


def _payload(url):
    return llm_client._build_payload(
        model="m", messages=[{"role": "user", "content": "hi"}], system="sys", tools=None, url=url
    )


def test_payload_sends_sampling_everywhere_and_provider_only_to_openrouter(env):
    env.setenv("OPENPOKE_LLM_TEMPERATURE", "0")
    env.setenv("OPENPOKE_LLM_SEED", "11")
    env.setenv("OPENPOKE_LLM_PROVIDER", "alpha")

    routed = _payload("https://openrouter.ai/api/v1/chat/completions")
    assert routed["temperature"] == 0.0 and routed["seed"] == 11
    assert routed["provider"] == {"order": ["alpha"], "allow_fallbacks": False, "require_parameters": True}
    assert routed["messages"][0] == {"role": "system", "content": "sys"}

    other = _payload("https://llm.example.test/v1/chat/completions")
    assert other["temperature"] == 0.0 and other["seed"] == 11
    assert "provider" not in other


def test_payload_omits_unset_sampling(env):
    payload = _payload("https://openrouter.ai/api/v1/chat/completions")
    assert set(payload) == {"model", "messages", "stream"}


def test_request_uses_base_url_and_extra_headers_and_returns_full_payload(env):
    env.setenv("OPENPOKE_LLM_BASE_URL", "https://llm.example.test/v1/")
    env.setenv("OPENPOKE_LLM_API_KEY", "test-key")
    env.setenv("OPENPOKE_LLM_HEADERS", json.dumps({"X-Session": "s-1"}))
    seen = {}
    body = {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3}}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, json=body)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    env.setattr(llm_client.httpx, "AsyncClient", lambda **kw: real_client(transport=transport))

    result = asyncio.run(
        llm_client.request_chat_completion(
            model="m", messages=[{"role": "user", "content": "hi"}], role="interaction"
        )
    )

    assert result == body
    assert seen["url"] == "https://llm.example.test/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer test-key"
    assert seen["headers"]["x-session"] == "s-1"
