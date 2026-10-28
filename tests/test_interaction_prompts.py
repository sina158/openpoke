import hashlib
from pathlib import Path

import pytest

from server.agents.interaction_agent.agent import build_system_prompt
from server.config import get_settings

PROMPTS = Path(__file__).resolve().parent.parent / "server" / "agents" / "interaction_agent" / "prompts"
# sha256 of server/agents/interaction_agent/system_prompt.md at upstream commit 5b5f635.
UPSTREAM_SHA256 = "d8257b89da9fcb47eaf980b1f9a097c8e147c45196e569e13303c04ec9a1da37"
# The shortlist prompt as round one shipped it (profile v1), and its v2 rewrite (change c4).
SHORTLIST_V1_SHA256 = "7d7be7c3fdc9b90e2591da71a4b234c5980d00ad1e0972d768e247abf179c04b"
SHORTLIST_V2_SHA256 = "00e40b09fc1c8b2f2be101ab1fc704d0835d606fe540a1d8ef20e00dea34d4b9"
PARALLEL_LINE = "Your goal should be to use this tool in parallel as much as possible."


@pytest.fixture
def variant(monkeypatch):
    def use(name, **env):
        monkeypatch.setenv("OPENPOKE_PROMPT_VARIANT", name)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        return build_system_prompt()

    yield use
    get_settings.cache_clear()


def _text(name):
    return (PROMPTS / name).read_text(encoding="utf-8").strip()


def test_prompt_files_are_pinned_byte_for_byte():
    def sha256(name):
        return hashlib.sha256((PROMPTS / name).read_bytes()).hexdigest()

    assert sha256("interaction_full.md") == UPSTREAM_SHA256
    assert sha256("interaction_shortlist.md") == SHORTLIST_V1_SHA256
    assert sha256("interaction_shortlist_v2.md") == SHORTLIST_V2_SHA256


@pytest.mark.parametrize("env", [{"OPENPOKE_ROUTING_PROFILE": "v1"}, {"OPENPOKE_V2_DISABLE": "c4"}])
def test_variant_selects_the_prompt_file(variant, env):
    original = variant("original", **env)
    shortlist = variant("shortlist", **env)

    assert original == _text("interaction_full.md")
    assert shortlist == _text("interaction_shortlist.md")
    assert "Don't worry if the agent name is unrelated" in original
    assert "Don't worry if the agent name is unrelated" not in shortlist
    for phrase in ("agent_id", "search_agents", "clarify_agent_choice", "confirm_new", "confirm_action"):
        assert phrase not in original and phrase in shortlist
    assert "Always let the user know what you're about to do" in shortlist


def test_profile_v2_reads_the_v2_shortlist_prompt_and_leaves_upstream_alone(variant):
    assert variant("original") == _text("interaction_full.md")
    shortlist = variant("shortlist")
    assert shortlist == _text("interaction_shortlist_v2.md")
    assert variant("shortlist", OPENPOKE_V2_DISABLE="c1,c3") == shortlist


def test_v2_shortlist_prompt_changes_only_the_three_routing_lines():
    v1 = _text("interaction_shortlist.md").split("\n")
    v2 = _text("interaction_shortlist_v2.md").split("\n")
    changed = [(old, new) for old, new in zip(v1, v2) if old != new]

    assert len(v1) == len(v2) and len(changed) == 3
    assert PARALLEL_LINE in changed[0][0] and PARALLEL_LINE not in "\n".join(v2)
    assert changed[0][1] == "- Send to several agents in one turn only when the message names several unrelated matters."
    assert changed[1][1].startswith("- Reuse an agent only for the same matter. The same person or business")
    assert changed[2][1].endswith("Sending the request to two of them to find out is guessing.")


@pytest.mark.parametrize("name", ["interaction_shortlist.md", "interaction_shortlist_v2.md"])
def test_shortlist_prompt_only_rewrites_the_routing_sections(name):
    full = (PROMPTS / "interaction_full.md").read_text(encoding="utf-8").split("\n")
    shortlist = (PROMPTS / name).read_text(encoding="utf-8").split("\n")
    head = full.index("Send Message to Agent Tool Usage")
    tail = full.index("Message Structure")
    assert shortlist[:head] == full[:head]
    assert shortlist[shortlist.index("Message Structure"):] == full[tail:]


def test_profile_v1_still_sends_the_recorded_fix_prompt(monkeypatch):
    # A moved byte in the v1 shortlist prompt changes the cassette key, so this would miss.
    from evals import run_routing
    from evals.cassette import CassetteMiss

    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v1")
    config = run_routing.RunConfig(arm="fix", sizes=(5,), split="report", limit=5, mode="replay")
    document = run_routing.run(config)
    assert document["header"]["live_calls"] == 0 and document["header"]["cassette_hits"] >= 5

    monkeypatch.setenv("OPENPOKE_ROUTING_PROFILE", "v2")
    with pytest.raises(CassetteMiss):
        run_routing.run(config)
