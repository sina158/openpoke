"""Pick the few execution agents the interaction agent should see on this turn.

score(a) = 1.0 * bm25(q, card_text(a)) + 2.0 * |entities(q) & entities(a)| + 0.5 * exp(-age_days(a) / 14)

Pinned agents come first (the agent whose result is being handled, agents holding a thread or draft
id the query names, agents with a pending action, agents named in the last few turns), then the best
scores, until k agents or the token budget.
Everything here is pure and deterministic: the same cards and query give the same list in the same
order, whatever order the cards arrive in. ``scorers="hybrid"`` fuses that ranking with a cosine
ranking over caller-supplied embeddings (reciprocal rank fusion, see ``embeddings.py``).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from .cards import MAIL_ID, AgentCard, card_text, extract_entities, render_card
from .embeddings import hybrid_scores

# exp(-age / 14 days): the weight falls to 1/e after 14 days, a half-life of about 9.7 days.
RECENCY_TIME_CONSTANT_DAYS = 14.0
SCORERS = ("bm25", "entities", "recency")
HYBRID = "hybrid"
DEFAULT_WEIGHTS = (1.0, 2.0, 0.5)

_STOPWORDS = frozenset(
    """
    a an and are as at be but by can could did do does for from had has have he her hi his i if in
    is it its me my of on or our please re she so that the their them they this to was we were what
    when which who will with would you your
    """.split()
)
_TOKEN = re.compile(r"[^\W_]+")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with common English stopwords removed."""
    return [token for token in _TOKEN.findall(text.lower()) if token not in _STOPWORDS]


class BM25:
    """Okapi BM25 over pre-tokenised documents."""

    def __init__(self, docs: Sequence[Sequence[str]], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._term_freqs = [Counter(doc) for doc in docs]
        self._lengths = [len(doc) for doc in docs]
        count = len(docs)
        self._avg_length = (sum(self._lengths) / count) if count else 0.0
        doc_freq: Counter[str] = Counter()
        for freqs in self._term_freqs:
            doc_freq.update(freqs.keys())
        self._idf = {
            term: math.log(1.0 + (count - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()
        }

    def score(self, query: Sequence[str], index: int) -> float:
        """Score document ``index`` against ``query``; each distinct query term counts once."""
        freqs = self._term_freqs[index]
        if not freqs:
            return 0.0
        norm = self.k1 * (1.0 - self.b + self.b * self._lengths[index] / self._avg_length)
        total = 0.0
        for term in dict.fromkeys(query):
            tf = freqs.get(term)
            if tf:
                total += self._idf[term] * tf * (self.k1 + 1.0) / (tf + norm)
        return total


@dataclass(frozen=True)
class Scored:
    card: AgentCard
    score: float
    pinned: str | None = None  # "current" | "lookup" | "pending" | "recent"


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def recency_weight(last_active_at: str, now: datetime) -> float:
    """exp(-age_days / 14); 0 when the timestamp cannot be read."""
    when = _parse_time(last_active_at)
    if when is None:
        return 0.0
    age_days = max((now - when).total_seconds() / 86400.0, 0.0)
    return math.exp(-age_days / RECENCY_TIME_CONSTANT_DAYS)


def _last_mention(haystack: str, needle: str) -> int:
    """Index of the last whole-word occurrence of ``needle`` in ``haystack``, or -1."""
    if not needle:
        return -1
    end = len(haystack)
    while True:
        pos = haystack.rfind(needle, 0, end)
        if pos < 0:
            return -1
        before = haystack[pos - 1] if pos > 0 else " "
        after_pos = pos + len(needle)
        after = haystack[after_pos] if after_pos < len(haystack) else " "
        if not before.isalnum() and not after.isalnum():
            return pos
        end = pos + len(needle) - 1


def pins(
    tail_text: str,
    cards: Sequence[AgentCard],
    pending_ids: Iterable[str] = (),
    current_id: str | None = None,
    query: str = "",
) -> list[tuple[str, str]]:
    """Agents that must be shown regardless of score, as ``(agent_id, reason)``, most important first.

    Order: the agent whose result is being handled ("current"), agents whose card holds a thread,
    draft or message id named in ``query`` ("lookup", by id), agents with a pending action
    ("pending", by id), then active agents whose id or multi-word name appears in ``tail_text``
    ("recent", latest mention first). ``tail_text`` should be the last three unsummarised turns. Each
    agent appears once; ids not among ``cards`` are ignored.
    """
    known = {card.agent_id for card in cards}
    result: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(agent_id: str, reason: str) -> None:
        if agent_id in known and agent_id not in seen:
            seen.add(agent_id)
            result.append((agent_id, reason))

    if current_id:
        add(current_id, "current")
    # Lookup before retrieval: a quoted thread id is an exact key, and at thousands of agents a few
    # common words can outscore it in BM25.
    named = set(extract_entities(query, kinds={MAIL_ID})) if query else set()
    if named:
        for agent_id in sorted(card.agent_id for card in cards if named.intersection(card.entities)):
            add(agent_id, "lookup")
    for agent_id in sorted(set(pending_ids)):
        add(agent_id, "pending")

    tail = tail_text.lower()
    if tail:
        mentioned: list[tuple[int, str]] = []
        for card in cards:
            if card.status == "archived":  # an agent idle for a month is not a recent mention
                continue
            names = [card.agent_id.lower()]
            words = card.name.lower().split()
            if len(words) >= 2:  # a one-word name like "Email" is too generic to count as a reference
                names.append(" ".join(words))
            last = max(_last_mention(tail, name) for name in names)
            if last >= 0:
                mentioned.append((last, card.agent_id))
        for _, agent_id in sorted(mentioned, key=lambda item: (-item[0], item[1])):
            add(agent_id, "recent")
    return result


def _parse_scorers(scorers: str) -> frozenset[str]:
    names = frozenset(part.strip() for part in scorers.split("+") if part.strip())
    unknown = names - set(SCORERS)
    if not names or unknown:
        raise ValueError(f"unknown scorers {sorted(unknown)!r}; join {SCORERS} with '+', or use {HYBRID!r}")
    return names


def _components(
    query: str, cards: Sequence[AgentCard], now: datetime, use: frozenset[str]
) -> dict[str, list[float]]:
    """Unweighted per-card scores for each scorer in ``use``."""
    out: dict[str, list[float]] = {}
    if "bm25" in use:
        terms = tokenize(query)
        index = BM25([tokenize(card_text(card)) for card in cards])
        out["bm25"] = [index.score(terms, i) for i in range(len(cards))]
    if "entities" in use:
        wanted = set(extract_entities(query))
        out["entities"] = [float(len(wanted.intersection(card.entities))) for card in cards]
    if "recency" in use:
        out["recency"] = [recency_weight(card.last_active_at, now) for card in cards]
    return out


def _combine(
    components: dict[str, list[float]], weights: tuple[float, float, float], size: int
) -> list[float]:
    weight_of = dict(zip(SCORERS, weights))
    totals = [0.0] * size
    for name, values in components.items():
        for i, value in enumerate(values):
            totals[i] += weight_of[name] * value
    return totals


def select(
    query: str,
    cards: Sequence[AgentCard],
    *,
    tail_text: str = "",
    pending_ids: Iterable[str] = (),
    current_id: str | None = None,
    k: int = 8,
    max_pins: int = 4,
    token_budget: int = 600,
    now: datetime | None = None,
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS,
    scorers: str = "bm25+entities+recency",
    vectors: Mapping[str, Sequence[float]] | None = None,
    query_vector: Sequence[float] | None = None,
) -> list[Scored]:
    """Return the shortlist: pins first (at most ``max_pins``), then by score desc, then by id.

    ``query`` is the current message plus the last two user turns. The list stops at ``k`` cards or
    when the next card would push the rendered size (chars / 4) past ``token_budget``; the first
    card is always kept. ``scorers`` picks the score components for ablation; ``max_pins=0``
    turns pinning off. Every card given is a candidate; archived cards compete on score and lookup
    but are never pinned as recent.

    ``scorers="hybrid"`` needs ``query_vector`` and ``vectors`` (agent id -> vector, from
    ``embeddings.embed``); the score is then the RRF of the full lexical ranking and the cosine
    ranking. No embedding happens here.
    """
    if not cards or k <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    hybrid = scorers == HYBRID
    use = frozenset(SCORERS) if hybrid else _parse_scorers(scorers)
    totals = _combine(_components(query, cards, now, use), weights, len(cards))
    if hybrid:
        if query_vector is None or vectors is None:
            raise ValueError("scorers='hybrid' needs query_vector and vectors")
        lexical = sorted(range(len(cards)), key=lambda i: (-totals[i], cards[i].agent_id))
        fused = hybrid_scores([cards[i].agent_id for i in lexical], query_vector, vectors)
        totals = [fused[card.agent_id] for card in cards]
    by_id = {card.agent_id: (card, total) for card, total in zip(cards, totals)}

    pinned = pins(tail_text, cards, pending_ids, current_id, query)[: max(max_pins, 0)]
    pinned_ids = {agent_id for agent_id, _ in pinned}
    ordered = [Scored(by_id[agent_id][0], by_id[agent_id][1], reason) for agent_id, reason in pinned]
    ordered += sorted(
        (Scored(card, total) for card, total in by_id.values() if card.agent_id not in pinned_ids),
        key=lambda s: (-s.score, s.card.agent_id),
    )

    chosen: list[Scored] = []
    used = 0.0
    for item in ordered:
        if len(chosen) >= k:
            break
        cost = len(render_card(item.card, item.pinned, now)) / 4
        if chosen and used + cost > token_budget:
            break
        chosen.append(item)
        used += cost
    return chosen


def search(
    query: str, cards: Sequence[AgentCard], limit: int = 5, now: datetime | None = None
) -> list[Scored]:
    """Cards that match ``query`` by text or entity, best first; backs the search_agents tool.

    Recency only reorders matches: a card with no textual or entity match is never returned.
    """
    if not cards or limit <= 0:
        return []
    now = now or datetime.now(timezone.utc)
    components = _components(query, cards, now, frozenset(SCORERS))
    totals = _combine(components, DEFAULT_WEIGHTS, len(cards))
    hits = [
        Scored(card, totals[i])
        for i, card in enumerate(cards)
        if components["bm25"][i] > 0 or components["entities"][i] > 0
    ]
    hits.sort(key=lambda s: (-s.score, s.card.agent_id))
    return hits[:limit]


def render_shortlist(selected: Sequence[Scored], total: int, now: datetime | None = None) -> str:
    """Body for ``<active_agents mode="shortlist" shown=".." total="..">``.

    When agents are hidden, the last line tells the model that search_agents(query) reaches them.
    Pass the same ``now`` given to ``select`` so the budget was measured on these exact lines.
    """
    lines = [render_card(item.card, item.pinned, now) for item in selected] or ["None"]
    hidden = total - len(selected)
    if hidden > 0:
        lines.append(
            f"{hidden} more agents are not shown. To find one, call search_agents(query) with a name, "
            "email, id or topic."
        )
    return "\n".join(lines)
