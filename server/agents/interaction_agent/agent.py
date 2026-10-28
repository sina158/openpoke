"""Interaction agent helpers for prompt construction."""

from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Dict, List

from ...config import get_settings
from ...services.execution import get_agent_roster

_PROMPT_DIR = Path(__file__).parent / "prompts"
# "original" is the upstream prompt byte for byte; "shortlist" rewrites only the routing guidance.
PROMPT_FILES = {"original": "interaction_full.md", "shortlist": "interaction_shortlist.md"}


@lru_cache(maxsize=None)
def _read_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt(variant: str | None = None) -> str:
    """Return the system prompt for ``variant``, by default the one ``OPENPOKE_PROMPT_VARIANT`` names."""
    return _read_prompt(PROMPT_FILES[variant or get_settings().prompt_variant])


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, roster, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    sections.append(f"<active_agents>\n{_render_active_agents()}\n</active_agents>")
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Format currently active execution agents into XML tags for LLM awareness
def _render_active_agents() -> str:
    roster = get_agent_roster()
    roster.load()
    agents = roster.get_agents()

    if not agents:
        return "None"

    rendered: List[str] = []
    for agent_name in agents:
        name = escape(agent_name or "agent", quote=True)
        rendered.append(f'<agent name="{name}" />')

    return "\n".join(rendered)


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    # Escaped like history, so an email cannot close the tag and forge a user turn.
    body = escape(latest_text.strip(), quote=False)
    return f"<{tag}>\n{body}\n</{tag}>"
