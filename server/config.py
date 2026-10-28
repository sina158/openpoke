"""Simplified configuration management."""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_env_file()


DEFAULT_APP_NAME = "OpenPoke Server"
DEFAULT_APP_VERSION = "0.3.0"
DEFAULT_MODEL = "anthropic/claude-sonnet-4"
DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"


def _env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except (TypeError, ValueError):
        return fallback


# The helpers below fail loudly on a malformed value: an eval run on a silently ignored flag
# measures the wrong thing.
def _env_str(name: str, fallback: str) -> str:
    return os.getenv(name, "").strip() or fallback


def _env_number(name: str, cast: Callable[[str], Any], fallback: Any = None) -> Any:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return cast(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a {cast.__name__}, got {raw!r}") from exc


def _env_choice(name: str, choices: tuple[str, ...], fallback: str) -> str:
    value = _env_str(name, fallback).lower()
    if value not in choices:
        raise ValueError(f"{name} must be one of {', '.join(choices)}, got {value!r}")
    return value


def _env_list(name: str) -> List[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _env_headers(name: str) -> Dict[str, str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        headers = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a JSON object") from exc
    if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
        raise ValueError(f"{name} must be a JSON object with string values")
    return headers


class Settings(BaseModel):
    """Application settings with lightweight env fallbacks."""

    # App metadata
    app_name: str = Field(default=DEFAULT_APP_NAME)
    app_version: str = Field(default=DEFAULT_APP_VERSION)

    # Server runtime
    server_host: str = Field(default=os.getenv("OPENPOKE_HOST", "0.0.0.0"))
    server_port: int = Field(default=_env_int("OPENPOKE_PORT", 8001))

    # LLM model selection, one override per role
    interaction_agent_model: str = Field(
        default_factory=lambda: _env_str("OPENPOKE_INTERACTION_MODEL", DEFAULT_MODEL)
    )
    execution_agent_model: str = Field(
        default_factory=lambda: _env_str("OPENPOKE_EXECUTION_MODEL", DEFAULT_MODEL)
    )
    execution_agent_search_model: str = Field(
        default_factory=lambda: _env_str("OPENPOKE_SEARCH_MODEL", DEFAULT_MODEL)
    )
    summarizer_model: str = Field(
        default_factory=lambda: _env_str("OPENPOKE_SUMMARIZER_MODEL", DEFAULT_MODEL)
    )
    email_classifier_model: str = Field(
        default_factory=lambda: _env_str("OPENPOKE_CLASSIFIER_MODEL", DEFAULT_MODEL)
    )

    # LLM endpoint and sampling (any OpenAI-compatible endpoint; OpenRouter by default)
    llm_base_url: str = Field(
        default_factory=lambda: _env_str("OPENPOKE_LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
    )
    llm_temperature: Optional[float] = Field(
        default_factory=lambda: _env_number("OPENPOKE_LLM_TEMPERATURE", float)
    )
    llm_seed: Optional[int] = Field(default_factory=lambda: _env_number("OPENPOKE_LLM_SEED", int))
    llm_provider_order: List[str] = Field(default_factory=lambda: _env_list("OPENPOKE_LLM_PROVIDER"))
    llm_headers: Dict[str, str] = Field(default_factory=lambda: _env_headers("OPENPOKE_LLM_HEADERS"))
    llm_prices_json: Optional[str] = Field(
        default_factory=lambda: os.getenv("OPENPOKE_PRICES_JSON", "").strip() or None
    )

    # Credentials / integrations
    openrouter_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("OPENPOKE_LLM_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    )
    composio_gmail_auth_config_id: Optional[str] = Field(default=os.getenv("COMPOSIO_GMAIL_AUTH_CONFIG_ID"))
    composio_api_key: Optional[str] = Field(default=os.getenv("COMPOSIO_API_KEY"))

    # HTTP behaviour
    cors_allow_origins_raw: str = Field(default=os.getenv("OPENPOKE_CORS_ALLOW_ORIGINS", "*"))
    enable_docs: bool = Field(default=os.getenv("OPENPOKE_ENABLE_DOCS", "1") != "0")
    docs_url: Optional[str] = Field(default=os.getenv("OPENPOKE_DOCS_URL", "/docs"))

    # Summarisation controls
    conversation_summary_threshold: int = Field(default=100)
    conversation_summary_tail_size: int = Field(default=10)

    # Agent routing and lifecycle
    roster_mode: str = Field(
        default_factory=lambda: _env_choice("OPENPOKE_ROSTER_MODE", ("full", "shortlist"), "shortlist")
    )
    prompt_variant: str = Field(
        default_factory=lambda: _env_choice(
            "OPENPOKE_PROMPT_VARIANT", ("original", "shortlist"), "shortlist"
        )
    )
    shortlist_k: int = Field(default_factory=lambda: _env_number("OPENPOKE_SHORTLIST_K", int, 8))
    duplicate_threshold: float = Field(
        default_factory=lambda: _env_number("OPENPOKE_DUPLICATE_THRESHOLD", float, 0.85)
    )
    archive_idle_days: int = Field(
        default_factory=lambda: _env_number("OPENPOKE_ARCHIVE_IDLE_DAYS", int, 30)
    )
    # None keeps every past request in an execution agent's prompt (the upstream behaviour).
    execution_history_limit: Optional[int] = Field(
        default_factory=lambda: _env_number("OPENPOKE_EXECUTION_HISTORY_LIMIT", int)
    )
    eval_budget_usd: float = Field(
        default_factory=lambda: _env_number("OPENPOKE_EVAL_BUDGET_USD", float, 40.0)
    )

    @property
    def cors_allow_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_allow_origins_raw.strip() in {"", "*"}:
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins_raw.split(",") if origin.strip()]

    @property
    def resolved_docs_url(self) -> Optional[str]:
        """Return documentation URL when docs are enabled."""
        return (self.docs_url or "/docs") if self.enable_docs else None

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
