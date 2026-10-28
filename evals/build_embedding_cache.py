"""Embed every card in the agent pool and every query in the golden set once, into one cache file.

    python evals/build_embedding_cache.py --pool evals/fixtures/agents_pool.jsonl \\
        --golden evals/golden/routing.jsonl --out evals/fixtures/embeddings_cache.bin

Pool lines are agent records: id (or agent_id), name, and optionally status, last_active_at, purpose,
entities and log ([tag, text] pairs). The text embedded is card_text(build_card(record)), the same
text the selector indexes. Golden lines are cases; their "query" field (or "message" when a case
has no "query") is embedded. Texts already cached are not embedded again and texts no longer in
either file are dropped, so the output holds exactly the current set.

Vectors are stored as int8 (see server/services/execution/embeddings.py): 416 bytes per text at
384 dims, about 2.3 MB for 5,000 agents plus 500 queries.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.services.execution.cards import build_card, card_text  # noqa: E402
from server.services.execution.embeddings import Embedder, EmbeddingCache, embed_cached  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def pool_card_text(record: dict) -> str:
    source = SimpleNamespace(
        id=record.get("id") or record["agent_id"],
        name=record["name"],
        status=record.get("status", "active"),
        last_active_at=record.get("last_active_at", ""),
        purpose=record.get("purpose", ""),
        entities=list(record.get("entities", [])),
    )
    log = [(tag, text) for tag, text in record.get("log", [])]
    return card_text(build_card(source, log))


def case_query(case: dict) -> str:
    return case.get("query") or case.get("message") or ""


def collect_texts(pool: Iterable[dict], cases: Iterable[dict]) -> list[str]:
    texts = [pool_card_text(record) for record in pool] + [case_query(case) for case in cases]
    return list(dict.fromkeys(text for text in texts if text))


def main(argv: Sequence[str] | None = None, embedder: Embedder | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the embeddings cache for the selector eval.")
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    texts = collect_texts(read_jsonl(args.pool), read_jsonl(args.golden))
    cache = EmbeddingCache(args.out)
    new = sum(text not in cache for text in texts)
    start = time.perf_counter()
    embed_cached(texts, cache, embedder)
    elapsed = time.perf_counter() - start
    cache.retain(texts)
    cache.save()

    rate = f"{elapsed / new * 1000:.1f}s per 1,000 texts, model load included" if new else "nothing new"
    size_mb = args.out.stat().st_size / 1e6
    print(f"{len(texts)} texts, {new} embedded in {elapsed:.1f}s ({rate}); {args.out} is {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
