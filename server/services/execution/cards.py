"""Agent cards: a short, deterministic summary of each execution agent, built from its own log.

A card is what the interaction agent sees instead of a bare name: a purpose line and the entities
(emails, thread and draft ids, policy numbers, names, phone numbers) the agent has worked with.
No LLM and no I/O; the caller reads the log and passes the entries in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Collection, Iterable, Protocol, Sequence

PURPOSE_MAX_CHARS = 200
# How the first request opens the purpose. "clip120" (profile v1) cuts it at 120 characters, which can
# drop the one detail that tells two clients apart, such as the city at the end of the first sentence.
# "sentence" (v2) keeps the first sentence up to FIRST_SENTENCE_MAX_CHARS and never drops a whole word.
PURPOSE_STYLES = ("clip120", "sentence")
FIRST_SENTENCE_MAX_CHARS = 180
# Identifiers are exact lookup keys while names are weak, repeated evidence, so each kind has its own
# cap and a log full of names can never push out the thread id a user will quote back.
MAX_CARD_IDS = 8
MAX_CARD_NAMES = 4
# Thread, draft and message ids name one conversation; emails and policy numbers are shared by every
# matter of a client, so only these are used for a deterministic lookup.
MAIL_ID = "mail_id"

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
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")

# Title Case words that start or end a sentence without being part of a name.
_NAME_STOPWORDS = frozenset(
    """
    A An The This That These Those Hi Hello Hey Dear Thanks Thank Please Re Fwd Subject From To Cc
    Send Email Reply Forward Draft Ask Tell Follow Remind Schedule Check Find Search Call Book Let
    Can Could Would Will Should Did Does Do Is Are Was Any What When Where Who Why How Yes No Ok Okay
    Emailed Sent Called Asked Replied Forwarded Drafted Scheduled Followed Checked Told Reminded Booked
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
    ("email", _EMAIL, str.lower),
    (MAIL_ID, _LABELLED_ID, _as_id),
    (MAIL_ID, _GMAIL_DRAFT_ID, str.lower),
    (MAIL_ID, _PREFIXED_ID, _as_id),
    (MAIL_ID, _GMAIL_HEX_ID, _as_hex_id),
    ("policy", _POLICY, str.lower),
    ("phone", _PHONE, _as_phone),
    ("name", _NAME, _as_name),
)


def _extract(text: str) -> list[tuple[str, str]]:
    """``(kind, value)`` for every entity in ``text``, in order of appearance."""
    found: list[tuple[int, str, str]] = []
    claimed: list[tuple[int, int]] = []
    for kind, pattern, normalise in _EXTRACTORS:
        for match in pattern.finditer(text):
            group = 1 if pattern.groups else 0
            start, end = match.span(group)
            if any(start < c_end and c_start < end for c_start, c_end in claimed):
                continue
            value = normalise(match.group(group))
            if value:
                claimed.append((start, end))
                found.append((start, kind, value))
    found.sort(key=lambda item: item[0])
    return [(kind, value) for _, kind, value in found]


def extract_entities(text: str, kinds: Collection[str] | None = None) -> list[str]:
    """Return the normalised entities in ``text``, deduplicated, in order of first appearance.

    ``kinds`` keeps only some: "email", MAIL_ID, "policy", "phone", "name".
    """
    return _dedupe(value for kind, value in _extract(text) if kinds is None or kind in kinds)


def _dedupe(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _latest(values: Sequence[str], limit: int) -> list[str]:
    """The ``limit`` most recently mentioned distinct values, in order of first appearance."""
    last_seen = {value: i for i, value in enumerate(values)}
    keep = set(sorted(last_seen, key=last_seen.__getitem__)[-limit:])
    return [value for value in _dedupe(values) if value in keep]


def _clip(text: str, limit: int, keep_whole_word: bool = False) -> str:
    """Collapse whitespace and cut at a word boundary so the result fits in ``limit`` chars.

    With ``keep_whole_word`` a word that ends exactly at the cut is kept; without it (round one) it is
    dropped like a word the cut splits.
    """
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: max(limit - 3, 0)]
    if " " in cut and not (keep_whole_word and flat[len(cut)] == " "):
        cut = cut[: cut.rfind(" ")]
    return cut + "..."


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    end = _SENTENCE_END.search(flat)
    return flat[: end.end()] if end else flat


def _purpose_from_log(requests: list[str], responses: list[str], style: str = "clip120") -> str:
    if not requests and not responses:
        return ""
    whole = style == "sentence"
    if not requests:
        first = ""
    elif whole:
        first = _clip(_first_sentence(requests[0]), FIRST_SENTENCE_MAX_CHARS, keep_whole_word=True)
    else:
        first = _clip(requests[0], 120)
    if not responses:
        return first
    if not first:
        return _clip(responses[-1], PURPOSE_MAX_CHARS, whole)
    room = PURPOSE_MAX_CHARS - len(first) - 3
    return f"{first} | {_clip(responses[-1], room, whole)}" if room > 10 else first


def build_card(
    source: CardSource, log_entries: Sequence[tuple[str, str]] = (), purpose_style: str = "clip120"
) -> AgentCard:
    """Build a card from the agent's log entries, given as ``(tag, text)`` pairs in log order.

    The purpose is the head of the first request (see ``PURPOSE_STYLES``) plus the head of the last
    response. Entities come from the whole log: identifiers first (the most recently mentioned survive
    the cap), then the first names. With no usable log the card falls back to ``source.purpose`` and
    ``source.entities``.
    """
    if purpose_style not in PURPOSE_STYLES:
        raise ValueError(f"purpose_style must be one of {PURPOSE_STYLES}, got {purpose_style!r}")
    requests = [text for tag, text in log_entries if tag == "agent_request"]
    responses = [text for tag, text in log_entries if tag == "agent_response"]
    whole = purpose_style == "sentence"
    purpose = _purpose_from_log(requests, responses, purpose_style) or _clip(
        source.purpose or "", PURPOSE_MAX_CHARS, whole
    )

    found = [item for tag, text in log_entries if tag in _ENTITY_TAGS for item in _extract(text)]
    ids = _latest([value for kind, value in found if kind != "name"], MAX_CARD_IDS)
    names = _dedupe(value for kind, value in found if kind == "name")[:MAX_CARD_NAMES]
    entities = ids + names
    if not entities:
        fallback = _dedupe(" ".join(str(e).split()).lower() for e in (source.entities or ()))
        entities = fallback[: MAX_CARD_IDS + MAX_CARD_NAMES]

    return AgentCard(
        agent_id=source.id,
        name=source.name,
        status=source.status,
        last_active_at=source.last_active_at,
        purpose=purpose,
        entities=tuple(entities),
    )


def card_text(card: AgentCard) -> str:
    """The text the selector indexes: name, purpose and entities."""
    return " ".join([card.name, card.purpose, *card.entities])


def _attr(value: str) -> str:
    return escape(" ".join(value.split()), quote=True)


def _age(last_active_at: str, now: datetime) -> str:
    """How long ago ``last_active_at`` was, e.g. "5h ago" or "12d ago"."""
    try:
        when = datetime.fromisoformat((last_active_at or "").replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    hours = max(int((now - when).total_seconds() // 3600), 0)
    return f"{hours}h ago" if hours < 24 else f"{hours // 24}d ago"


def render_card(card: AgentCard, pinned: str | None = None, now: datetime | None = None) -> str:
    """Render one ``<agent ... />`` line with every attribute value escaped.

    With ``now`` the last activity is shown as an age derived from it, so a fixed clock gives a
    byte-stable line; without it, as the timestamp to the minute.
    """
    last_active = _age(card.last_active_at, now) if now else (card.last_active_at or "")[:16]
    parts = [
        f'id="{_attr(card.agent_id)}"',
        f'name="{_attr(card.name or "agent")}"',
        f'status="{_attr(card.status)}"',
        f'last_active="{_attr(last_active)}"',
    ]
    if pinned:
        parts.append(f'pinned="{_attr(pinned)}"')
    if card.purpose:
        parts.append(f'purpose="{_attr(card.purpose)}"')
    if card.entities:
        parts.append(f'entities="{_attr("; ".join(card.entities))}"')
    return f"<agent {' '.join(parts)} />"
