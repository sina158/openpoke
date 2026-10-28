"""Agent cards: a short, deterministic summary of each execution agent, built from its own log.

A card is what the interaction agent sees instead of a bare name: a purpose line and the entities
(emails, thread and draft ids, policy numbers, names, phone numbers) the agent has worked with.
No LLM and no I/O; the caller reads the log and passes the entries in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import Iterable, Protocol, Sequence

PURPOSE_MAX_CHARS = 200
MAX_CARD_ENTITIES = 8

# Tool responses are left out: they hold raw search results, so every address in an inbox would
# become an "entity" of the agent that searched it.
_ENTITY_TAGS = frozenset({"agent_request", "agent_action", "agent_response"})


class CardSource(Protocol):
    """What a card needs from the registry record."""

    id: str
    name: str
    status: str
    last_active_at: str
    purpose: str
    entities: list[str]


@dataclass(frozen=True)
class AgentCard:
    agent_id: str
    name: str
    status: str
    last_active_at: str
    purpose: str
    entities: tuple[str, ...]


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_LABELLED_ID = re.compile(
    r"\b(?:thread|draft|message|msg)[ _]?id\b[\"']?\s*[:=]?\s*[\"']?([A-Za-z0-9][A-Za-z0-9_-]{3,})",
    re.IGNORECASE,
)
_PREFIXED_ID = re.compile(r"\b(?:thread|draft|message|msg|t|d|m)_[A-Za-z0-9]+\b", re.IGNORECASE)
_GMAIL_HEX_ID = re.compile(r"\b[0-9a-f]{16}\b", re.IGNORECASE)
_GMAIL_DRAFT_ID = re.compile(r"\br-?\d{15,}\b")
_POLICY = re.compile(r"\b[A-Za-z]{2,5}-?\d{3,}(?:-[A-Za-z0-9]+)*\b")
_PHONE = re.compile(r"(?<![\w+])\+?\(?\d[\d ().-]{7,}\d(?!\w)")
_NAME = re.compile(r"\b[A-Z][A-Za-z'-]*[a-z](?: [A-Z][A-Za-z'-]*[a-z])+\b")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Title Case words that start or end a sentence without being part of a name.
_NAME_STOPWORDS = frozenset(
    """
    A An The This That These Those Hi Hello Hey Dear Thanks Thank Please Re Fwd Subject From To Cc
    Send Email Reply Forward Draft Ask Tell Follow Remind Schedule Check Find Search Call Book Let
    Can Could Would Will Should Did Does Do Is Are Was Any What When Where Who Why How Yes No Ok Okay
    Monday Tuesday Wednesday Thursday Friday Saturday Sunday Today Tomorrow Yesterday
    January February March April May June July August September October November December
    """.split()
)


def _has_digit(value: str) -> bool:
    return any(ch.isdigit() for ch in value)


def _as_id(value: str) -> str | None:
    return value.lower() if _has_digit(value) else None


def _as_hex_id(value: str) -> str | None:
    return value.lower() if _has_digit(value) and not value.isdigit() else None


def _as_phone(value: str) -> str | None:
    if _ISO_DATE.match(value):
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not 10 <= len(digits) <= 15:
        return None
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _as_name(value: str) -> str | None:
    words = value.split()
    while words and words[0] in _NAME_STOPWORDS:
        words.pop(0)
    while words and words[-1] in _NAME_STOPWORDS:
        words.pop()
    return " ".join(words).lower() if len(words) >= 2 else None


# Order matters: an earlier pattern claims its span, so an email's local part is never re-read as
# a policy number and a date is never re-read as a phone number.
_EXTRACTORS = (
    (_EMAIL, str.lower),
    (_LABELLED_ID, _as_id),
    (_GMAIL_DRAFT_ID, str.lower),
    (_PREFIXED_ID, _as_id),
    (_GMAIL_HEX_ID, _as_hex_id),
    (_POLICY, str.lower),
    (_PHONE, _as_phone),
    (_NAME, _as_name),
)


def extract_entities(text: str) -> list[str]:
    """Return the normalised entities in ``text``, deduplicated, in order of first appearance."""
    found: list[tuple[int, str]] = []
    claimed: list[tuple[int, int]] = []
    for pattern, normalise in _EXTRACTORS:
        for match in pattern.finditer(text):
            group = 1 if pattern.groups else 0
            start, end = match.span(group)
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue
            value = normalise(match.group(group))
            if value:
                claimed.append((start, end))
                found.append((start, value))
    found.sort(key=lambda item: item[0])
    return _dedupe(value for _, value in found)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and cut at a word boundary so the result fits in ``limit`` chars."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: max(limit - 3, 0)]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "..."


def _purpose_from_log(requests: list[str], responses: list[str]) -> str:
    if not requests and not responses:
        return ""
    first = _clip(requests[0], 120) if requests else ""
    if not responses:
        return first
    if not first:
        return _clip(responses[-1], PURPOSE_MAX_CHARS)
    room = PURPOSE_MAX_CHARS - len(first) - 3
    return f"{first} | {_clip(responses[-1], room)}" if room > 10 else first


def build_card(source: CardSource, log_entries: Sequence[tuple[str, str]] = ()) -> AgentCard:
    """Build a card from the agent's log entries, given as ``(tag, text)`` pairs in log order.

    The purpose is the first request plus the head of the last response. With no usable log the
    card falls back to ``source.purpose`` and ``source.entities``.
    """
    requests = [text for tag, text in log_entries if tag == "agent_request"]
    responses = [text for tag, text in log_entries if tag == "agent_response"]
    purpose = _purpose_from_log(requests, responses) or _clip(source.purpose or "", PURPOSE_MAX_CHARS)

    entities = _dedupe(
        entity
        for tag, text in log_entries
        if tag in _ENTITY_TAGS
        for entity in extract_entities(text)
    )
    if not entities:
        entities = _dedupe(" ".join(str(e).split()).lower() for e in (source.entities or ()))

    return AgentCard(
        agent_id=source.id,
        name=source.name,
        status=source.status,
        last_active_at=source.last_active_at,
        purpose=purpose,
        entities=tuple(entities[:MAX_CARD_ENTITIES]),
    )


def card_text(card: AgentCard) -> str:
    """The text the selector indexes: name, purpose and entities."""
    return " ".join([card.name, card.purpose, *card.entities])


def _attr(value: str) -> str:
    return escape(" ".join(value.split()), quote=True)


def render_card(card: AgentCard, pinned: str | None = None) -> str:
    """Render one ``<agent ... />`` line with every attribute value escaped."""
    parts = [
        f'id="{_attr(card.agent_id)}"',
        f'name="{_attr(card.name or "agent")}"',
        f'status="{_attr(card.status)}"',
        f'last_active="{_attr((card.last_active_at or "")[:16])}"',
    ]
    if pinned:
        parts.append(f'pinned="{_attr(pinned)}"')
    if card.purpose:
        parts.append(f'purpose="{_attr(card.purpose)}"')
    if card.entities:
        parts.append(f'entities="{_attr("; ".join(card.entities))}"')
    return f"<agent {' '.join(parts)} />"
