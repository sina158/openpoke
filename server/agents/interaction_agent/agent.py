"""Interaction agent helpers for prompt construction."""

import re
from datetime import datetime
from functools import lru_cache
from html import escape, unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...config import get_settings, shortlist_evidence, v2_change_enabled
from ...logging_config import logger
from ...services.approvals import get_approval_store
from ...services.execution import embeddings, get_agent_registry, get_agent_roster, get_execution_agent_logs
from ...services.execution.card_store import cards_for
from ...services.execution.cards import AgentCard, card_text, render_card
from ...services.execution.selector import Scored, evidence, render_shortlist, select
from ...utils.clock import now_utc

_PROMPT_DIR = Path(__file__).parent / "prompts"
# The baseline arm (upstream prompt, full roster, upstream tools) still differs from upstream on
# purpose: the current turn is escaped like history, so an email cannot forge a user turn, and
# outbound email only proposes a send. Archiving idle agents runs only when the roster is shortlisted.
# "original" is the upstream prompt byte for byte; "shortlist" rewrites only the routing guidance.
PROMPT_FILES = {"original": "interaction_full.md", "shortlist": "interaction_shortlist.md"}
# The v2 shortlist prompt (change c4): reuse only for the same matter, no "parallel as much as possible".
SHORTLIST_V2_FILE = "interaction_shortlist_v2.md"


@lru_cache(maxsize=None)
def _read_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt(variant: str | None = None) -> str:
    """Return the system prompt for ``variant``, by default the one ``OPENPOKE_PROMPT_VARIANT`` names.

    The shortlist variant reads its v2 file unless profile v1 is set or c4 is disabled.
    """
    variant = variant or get_settings().prompt_variant
    if variant == "shortlist" and v2_change_enabled("c4"):
        return _read_prompt(SHORTLIST_V2_FILE)
    return _read_prompt(PROMPT_FILES[variant])


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, roster, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    roster_mode = get_settings().roster_mode
    if roster_mode == "full":
        sections.append(f"<active_agents>\n{_render_active_agents()}\n</active_agents>")
    elif roster_mode == "full_cards":
        sections.append(_render_all_cards())
    else:
        sections.append(_render_shortlist(latest_text, transcript, message_type))
    if get_settings().prompt_variant == "shortlist":
        pending = _render_pending_actions()
        if pending:
            sections.append(pending)
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
    # The shortlist prompt routes by id, so its full roster carries ids (names and ids only, no
    # cards). The upstream prompt keeps the upstream lines.
    if get_settings().prompt_variant == "shortlist":
        records = get_agent_registry().list("active")
        lines = [
            f'<agent id="{escape(r.id, quote=True)}" name="{escape(r.name or "agent", quote=True)}" />'
            for r in records
        ]
        return "\n".join(lines) or "None"

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


_TURN = re.compile(r"<(user_message|agent_message|poke_reply)\b[^>]*>(.*?)</\1>", re.DOTALL)
_REPORTED_AGENT = re.compile(r"^\[(?:SUCCESS|FAILED)\] (.+?): ", re.MULTILINE)
_hybrid_warned = False


def _render_all_cards() -> str:
    """Every active agent as its card, with no selection (the full_cards ablation arm)."""
    now = now_utc()
    cards = cards_for(get_agent_registry().list("active"))
    body = "\n".join(render_card(card, now=now) for card in cards) or "None"
    return f'<active_agents mode="full_cards" total="{len(cards)}">\n{body}\n</active_agents>'


def _render_shortlist(latest_text: str, transcript: str, message_type: str) -> str:
    """The few agents most likely to matter for this turn, as cards with ids (plan 3.5)."""
    settings = get_settings()
    now = now_utc()
    registry = get_agent_registry()
    # Under v2 archived agents are candidates too, shown with status="archived" and revived on reuse,
    # so a matter from last year is not hidden behind this year's agent for the same client.
    cards = cards_for(registry.list(None if v2_change_enabled("c3") else "active"))
    turns = [(tag, unescape(body)) for tag, body in _TURN.findall(transcript)]
    user_turns = [text for tag, text in turns if tag == "user_message"]
    query = "\n".join([*user_turns[-2:], latest_text])
    tail = "\n".join([text for _, text in turns[-3:]] + [latest_text])
    current_id = None
    if message_type == "agent":
        reported = (registry.find_by_name(name) for name in _REPORTED_AGENT.findall(latest_text))
        current_id = next((record.id for record in reported if record), None)
    pending = {action.agent_id for action in get_approval_store().list_pending() if action.agent_id}
    selected = select(
        query,
        cards,
        tail_text=tail,
        pending_ids=pending,
        current_id=current_id,
        k=settings.shortlist_k,
        token_budget=settings.shortlist_token_budget,
        now=now,
        **_hybrid_arguments(query, cards),
    )
    found = _shortlist_evidence(latest_text, selected) if shortlist_evidence() else None
    body = render_shortlist(selected, len(cards), now, found)
    header = f'<active_agents mode="shortlist" shown="{len(selected)}" total="{len(cards)}">'
    return f"{header}\n{body}\n</active_agents>"


def _shortlist_evidence(message: str, selected: Sequence[Scored]) -> Dict[str, Tuple[int, str]]:
    """For each shortlisted agent, its own log entry that best matches the incoming message."""
    logs = get_execution_agent_logs()
    found: Dict[str, Tuple[int, str]] = {}
    for item in selected:
        entries = [(tag, text) for tag, _, text in logs.iter_entries(item.card.name)]
        match = evidence(message, entries)
        if match:
            found[item.card.agent_id] = match
    return found


def _hybrid_arguments(query: str, cards: List[AgentCard]) -> Dict[str, Any]:
    """Selector arguments for OPENPOKE_SELECTOR=hybrid; BM25 (no arguments) when vectors are unavailable."""
    global _hybrid_warned
    settings = get_settings()
    if settings.selector != "hybrid" or not cards:
        return {}
    cache_dir: Optional[Path] = Path(settings.embeddings_dir) if settings.embeddings_dir else None
    try:
        vectors = embeddings.embed([query, *(card_text(card) for card in cards)], cache_dir)
    except Exception as exc:
        if not _hybrid_warned:
            logger.warning(f"Hybrid selector unavailable, using BM25: {exc}")
            _hybrid_warned = True
        return {}
    return {
        "scorers": "hybrid",
        "query_vector": vectors[0],
        "vectors": {card.agent_id: vector for card, vector in zip(cards, vectors[1:])},
    }


PENDING_ACTIONS_SHOWN = 5


def _render_pending_actions() -> str:
    """Sends awaiting approval, newest first, as the approval store holds them.

    The summary was built in code from the exact arguments when the action was proposed, so what the
    user is asked to confirm never depends on how an agent described it. Empty when nothing is pending.
    """
    pending = get_approval_store().list_pending()
    if not pending:
        return ""
    now = now_utc()
    lines = [
        f'<action id="{escape(action.id, quote=True)}" code="{escape(action.confirm_code or "", quote=True)}"'
        f' kind="{escape(action.kind, quote=True)}" age="{_pending_age(action.created_at, now)}">'
        f"{escape(action.summary or 'no summary recorded', quote=False)}</action>"
        for action in reversed(pending[-PENDING_ACTIONS_SHOWN:])
    ]
    header = f'<pending_actions shown="{len(lines)}" total="{len(pending)}">'
    return "\n".join([header, *lines, "</pending_actions>"])


def _pending_age(created_at: str, now: datetime) -> str:
    minutes = max(int((now - datetime.fromisoformat(created_at)).total_seconds() // 60), 0)
    return f"{minutes}m" if minutes < 120 else f"{minutes // 60}h"


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    # Escaped like history, so an email cannot close the tag and forge a user turn.
    body = escape(latest_text.strip(), quote=False)
    return f"<{tag}>\n{body}\n</{tag}>"
