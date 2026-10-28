import importlib.util
import json
import random
from pathlib import Path
from types import SimpleNamespace

from server.services.execution.cards import build_card, card_text
from server.services.execution.embeddings import DIM, EmbeddingCache

_SCRIPT = Path(__file__).resolve().parent.parent / "evals" / "build_embedding_cache.py"
_spec = importlib.util.spec_from_file_location("build_embedding_cache", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

POOL = [
    {"id": "a_0001", "name": "Renewal for Dana Whitfield", "purpose": "Renewal", "entities": ["HO-4421-77"]},
    {
        "id": "a_0002",
        "name": "Certificate for Gull Harbour Marine",
        "last_active_at": "2026-10-20T00:00:00+00:00",
        "log": [["agent_request", "Send the COI to ops@gullharbour.test"], ["agent_response", "Sent."]],
    },
    {"agent_id": "a_0003", "name": "Billing question for Elif Moreau"},
]
GOLDEN = [
    {"id": "case_1", "query": "did the marina get its certificate?"},
    {"id": "case_2", "message": "tell Dana the renewal is done"},
]


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [[random.Random(text).uniform(-1, 1) for _ in range(DIM)] for text in texts]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def run(tmp_path, pool, golden, embedder):
    write_jsonl(tmp_path / "pool.jsonl", pool)
    write_jsonl(tmp_path / "golden.jsonl", golden)
    out = tmp_path / "fixtures" / "embeddings_cache.bin"
    argv = ["--pool", tmp_path / "pool.jsonl", "--golden", tmp_path / "golden.jsonl", "--out", out]
    assert script.main([str(arg) for arg in argv], embedder=embedder) == 0
    return out


def test_pool_text_is_exactly_what_the_selector_indexes():
    record = POOL[1]
    source = SimpleNamespace(
        id="a_0002", name=record["name"], status="active", last_active_at=record["last_active_at"],
        purpose="", entities=[],
    )
    expected = card_text(build_card(source, [tuple(entry) for entry in record["log"]]))
    assert script.pool_card_text(record) == expected
    assert "ops@gullharbour.test" in expected


def test_builds_a_cache_with_every_card_and_query(tmp_path, capsys):
    embedder = FakeEmbedder()
    out = run(tmp_path, POOL, GOLDEN, embedder)
    cache = EmbeddingCache(out)
    expected = [script.pool_card_text(record) for record in POOL] + [
        "did the marina get its certificate?",
        "tell Dana the renewal is done",
    ]
    assert len(cache) == 5 and all(text in cache for text in expected)
    assert embedder.calls == [expected]
    assert out.stat().st_size == len(cache._header()) + 5 * (32 + DIM)
    assert "per 1,000 texts" in capsys.readouterr().out


def test_rerun_embeds_nothing_and_drops_texts_no_longer_used(tmp_path):
    out = run(tmp_path, POOL, GOLDEN, FakeEmbedder())
    before = out.read_bytes()
    embedder = FakeEmbedder()
    run(tmp_path, POOL, GOLDEN, embedder)
    assert embedder.calls == [] and out.read_bytes() == before
    run(tmp_path, POOL[:2], GOLDEN, embedder)
    assert embedder.calls == [] and len(EmbeddingCache(out)) == 4
