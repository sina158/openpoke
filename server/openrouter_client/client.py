from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from ..config import DEFAULT_LLM_BASE_URL, get_settings

OpenRouterBaseURL = DEFAULT_LLM_BASE_URL


class OpenRouterError(RuntimeError):
    """Raised when the OpenRouter API returns an error response."""


def _headers(*, api_key: Optional[str] = None) -> Dict[str, str]:
    settings = get_settings()
    key = (api_key or settings.openrouter_api_key or "").strip()
    if not key:
        raise OpenRouterError("Missing OpenRouter API key")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers.update(settings.llm_headers)
    return headers


def _build_messages(messages: List[Dict[str, str]], system: Optional[str]) -> List[Dict[str, str]]:
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _is_openrouter(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def _build_payload(
    *,
    model: str,
    messages: List[Dict[str, str]],
    system: Optional[str],
    tools: Optional[List[Dict[str, Any]]],
    url: str,
) -> Dict[str, Any]:
    """Request body; OpenRouter-only fields are sent only to OpenRouter."""
    settings = get_settings()
    payload: Dict[str, Any] = {
        "model": model,
        "messages": _build_messages(messages, system),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if settings.llm_temperature is not None:
        payload["temperature"] = settings.llm_temperature
    if settings.llm_seed is not None:
        payload["seed"] = settings.llm_seed
    if settings.llm_provider_order and _is_openrouter(url):
        payload["provider"] = {
            "order": list(settings.llm_provider_order),
            "allow_fallbacks": False,
            "require_parameters": True,
        }
    return payload


def _handle_response_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    detail: str
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or json.dumps(payload)
    except Exception:
        detail = response.text
    raise OpenRouterError(f"OpenRouter request failed ({response.status_code}): {detail}") from exc


async def request_chat_completion(
    *,
    model: str,
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    base_url: Optional[str] = None,
    role: str,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Request a chat completion and return the full JSON payload, ``usage`` included.

    Every call is written to the cost ledger under ``role``; a call is refused once the
    ledger has passed its budget.
    """
    # Lazy: server.services imports this client, so a top-level import would depend on import order.
    from ..services import costs

    costs.check_budget()
    base_url = base_url or get_settings().llm_base_url
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = _build_payload(model=model, messages=messages, system=system, tools=tools, url=url)

    started = time.perf_counter()
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_headers(api_key=api_key),
                json=payload,
                timeout=60.0,  # Set reasonable timeout instead of None
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_response_error(exc)
            data = response.json()
            costs.record_call(
                role=role,
                model=model,
                usage=data.get("usage"),
                latency_ms=(time.perf_counter() - started) * 1000,
                turn_id=turn_id,
            )
            return data
        except httpx.HTTPStatusError as exc:  # pragma: no cover - handled above
            _handle_response_error(exc)
        except httpx.HTTPError as exc:
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

    raise OpenRouterError("OpenRouter request failed: unknown error")


__all__ = ["OpenRouterError", "request_chat_completion", "OpenRouterBaseURL"]
