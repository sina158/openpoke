import random
from datetime import datetime, timezone

import pytest

from server.services.execution import embeddings
from server.services.execution.cards import AgentCard
from server.services.execution.embeddings import (
    DIM,
    EmbeddingCache,
    cosine,
    dequantize,
    embed_cached,
    hybrid_scores,
    quantize,
    rrf,
)
from server.services.execution.selector import select

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def random_vector(seed, dim=DIM):
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(dim)]


class CountingEmbedder:
    def __init__(self):
        self.calls = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [random_vector(text) for text in texts]


def test_cosine_basics():
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 3.0]) == 0.0
    assert cosine([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_int8_quantisation_keeps_direction():
    vector = random_vector(1)
    raw = quantize(vector)
    assert len(raw) == DIM
    assert cosine(vector, dequantize(raw)) > 0.999
    assert max(abs(x) for x in dequantize(raw)) == 1.0
    assert dequantize(quantize([0.0] * 4)) == [0.0] * 4


def test_rrf_adds_one_over_k_plus_rank():
    scores = rrf([["a", "b", "c"], ["c", "a"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert sorted(scores, key=lambda key: -scores[key]) == ["a", "c", "b"]


def test_hybrid_scores_rank_by_cosine_and_keep_ids_without_vectors():
    query = [1.0, 0.0]
    vectors = {"a": [0.0, 1.0], "b": [0.9, 0.1], "c": [1.0, 0.0]}
    scores = hybrid_scores(["a", "b", "c", "d"], query, vectors)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 63)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert scores["d"] == pytest.approx(1 / 64)


def test_cache_round_trips_through_disk(tmp_path):
    path = tmp_path / "cache.bin"
    cache = EmbeddingCache(path)
    cache.put("renewal for the home policy", random_vector(2))
    cache.save()
    assert path.stat().st_size == len(cache._header()) + 32 + DIM
    reloaded = EmbeddingCache(path)
    assert "renewal for the home policy" in reloaded
    assert reloaded.get("renewal for the home policy") == cache.get("renewal for the home policy")
    assert reloaded.get("something else") is None


def test_cache_bytes_do_not_depend_on_insertion_order(tmp_path):
    texts = [f"text {i}" for i in range(5)]
    first, second = EmbeddingCache(tmp_path / "a.bin"), EmbeddingCache(tmp_path / "b.bin")
    for text in texts:
        first.put(text, random_vector(text))
    for text in reversed(texts):
        second.put(text, random_vector(text))
    first.save()
    second.save()
    assert (tmp_path / "a.bin").read_bytes() == (tmp_path / "b.bin").read_bytes()


def test_cache_is_keyed_by_model_and_refuses_another_models_file(tmp_path):
    path = tmp_path / "cache.bin"
    cache = EmbeddingCache(path)
    other = EmbeddingCache(tmp_path / "x.bin", model_name="other/model")
    assert cache.key("same text") != other.key("same text")
    cache.put("same text", random_vector(3))
    cache.save()
    with pytest.raises(ValueError):
        EmbeddingCache(path, model_name="other/model")


def test_cache_rejects_wrong_dimension_and_truncated_files(tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.bin")
    with pytest.raises(ValueError):
        cache.put("short", [1.0, 2.0])
    cache.put("ok", random_vector(4))
    cache.save()
    (tmp_path / "cache.bin").write_bytes((tmp_path / "cache.bin").read_bytes()[:-1])
    with pytest.raises(ValueError):
        EmbeddingCache(tmp_path / "cache.bin")


def test_retain_drops_texts_no_longer_needed(tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.bin")
    for text in ("keep", "drop"):
        cache.put(text, random_vector(text))
    cache.retain(["keep"])
    assert "keep" in cache and "drop" not in cache and len(cache) == 1


def test_embed_cached_only_embeds_misses_once(tmp_path):
    cache = EmbeddingCache(tmp_path / "cache.bin")
    embedder = CountingEmbedder()
    first = embed_cached(["a", "b", "a"], cache, embedder)
    second = embed_cached(["b", "c"], cache, embedder)
    assert embedder.calls == [["a", "b"], ["c"]]
    assert first[0] == first[2] == cache.get("a")
    assert second[0] == first[1]


def test_embed_cached_without_fastembed_reads_hits_and_fails_loudly_on_misses(tmp_path, monkeypatch):
    cache = EmbeddingCache(tmp_path / "cache.bin")
    cache.put("cached", random_vector(5))
    monkeypatch.setattr(embeddings, "available", lambda: False)
    assert embed_cached(["cached"], cache) == [cache.get("cached")]
    with pytest.raises(RuntimeError, match="requirements-embeddings.txt"):
        embed_cached(["not cached"], cache)


def _marina_cards():
    def card(agent_id, name):
        return AgentCard(agent_id, name, "active", "2026-10-20T00:00:00+00:00", "", ())

    return [
        card("a_0001", "Marina lease certificate"),
        card("a_0002", "Marina lease"),
        card("a_0003", "Marina dues"),
        card("a_0004", "COI for the boat harbour rental"),  # same matter, no shared words
    ]


def test_hybrid_select_surfaces_a_paraphrase_that_bm25_misses():
    cards = _marina_cards()
    query = "marina lease certificate"
    lexical = select(query, cards, k=4, now=NOW)
    assert [s.card.agent_id for s in lexical] == ["a_0001", "a_0002", "a_0003", "a_0004"]
    query_vector = [1.0, 0.0, 0.0]
    vectors = {
        "a_0001": [0.9, 0.4, 0.0],
        "a_0002": [0.1, 1.0, 0.0],
        "a_0003": [0.5, 0.5, 0.7],
        "a_0004": [1.0, 0.0, 0.0],
    }
    hybrid = select(query, cards, k=2, now=NOW, scorers="hybrid", vectors=vectors, query_vector=query_vector)
    assert [s.card.agent_id for s in hybrid] == ["a_0001", "a_0004"]
    assert hybrid[1].score == pytest.approx(1 / 64 + 1 / 61)
    assert [s.card.agent_id for s in select(query, cards, k=2, now=NOW)] == ["a_0001", "a_0002"]


def test_hybrid_select_keeps_pins_first_and_needs_vectors():
    cards = _marina_cards()
    vectors = {card.agent_id: [1.0, 0.0] for card in cards}
    result = select(
        "marina", cards, k=2, now=NOW, scorers="hybrid", vectors=vectors, query_vector=[1.0, 0.0],
        pending_ids=["a_0003"],
    )
    assert result[0].card.agent_id == "a_0003" and result[0].pinned == "pending"
    with pytest.raises(ValueError, match="hybrid"):
        select("marina", cards, now=NOW, scorers="hybrid")


def _local_model_or_skip():
    if not embeddings.available():
        pytest.skip("fastembed is not installed (pip install -r requirements-embeddings.txt)")
    try:
        embeddings.embed_with_model(["warm up"], local_files_only=True)
    except Exception as exc:  # no network in tests: the model must already be downloaded
        pytest.skip(f"{embeddings.MODEL_NAME} is not downloaded locally: {exc}")


def test_real_model_puts_paraphrases_closer_than_unrelated_text(tmp_path):
    _local_model_or_skip()
    texts = [
        "certificate of insurance for the marina lease",
        "COI for the boat harbour rental",
        "recipe for banana bread",
    ]
    vectors = embeddings.embed(texts, cache_dir=tmp_path)
    assert all(len(vector) == DIM for vector in vectors)
    assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[2])
    assert embeddings.embed(texts, cache_dir=tmp_path) == vectors
    assert len(EmbeddingCache(tmp_path / embeddings.CACHE_FILENAME)) == 3
