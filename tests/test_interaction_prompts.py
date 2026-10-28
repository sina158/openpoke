import hashlib
from pathlib import Path

import pytest

from server.agents.interaction_agent.agent import build_system_prompt
from server.config import get_settings

PROMPTS = Path(__file__).resolve().parent.parent / "server" / "agents" / "interaction_agent" / "prompts"
# sha256 of server/agents/interaction_agent/system_prompt.md at upstream commit 5b5f635.
UPSTREAM_SHA256 = "d8257b89da9fcb47eaf980b1f9a097c8e147c45196e569e13303c04ec9a1da37"


@pytest.fixture
def variant(monkeypatch):
    def use(name):
        monkeypatch.setenv("OPENPOKE_PROMPT_VARIANT", name)
        get_settings.cache_clear()
        return build_system_prompt()

    yield use
    get_settings.cache_clear()


def test_full_prompt_is_the_upstream_file_byte_for_byte():
    assert hashlib.sha256((PROMPTS / "interaction_full.md").read_bytes()).hexdigest() == UPSTREAM_SHA256


def test_variant_selects_the_prompt_file(variant):
    original = variant("original")
    shortlist = variant("shortlist")

    assert original == (PROMPTS / "interaction_full.md").read_text(encoding="utf-8").strip()
    assert shortlist == (PROMPTS / "interaction_shortlist.md").read_text(encoding="utf-8").strip()
    assert "Don't worry if the agent name is unrelated" in original
    assert "Don't worry if the agent name is unrelated" not in shortlist
    for phrase in ("agent_id", "search_agents", "clarify_agent_choice", "confirm_new", "confirm_action"):
        assert phrase not in original and phrase in shortlist
    assert "Always let the user know what you're about to do" in shortlist


def test_shortlist_prompt_only_rewrites_the_routing_sections():
    full = (PROMPTS / "interaction_full.md").read_text(encoding="utf-8").split("\n")
    shortlist = (PROMPTS / "interaction_shortlist.md").read_text(encoding="utf-8").split("\n")
    head = full.index("Send Message to Agent Tool Usage")
    tail = full.index("Message Structure")
    assert shortlist[:head] == full[:head]
    assert shortlist[shortlist.index("Message Structure"):] == full[tail:]
