"""Optional embeddings scorer: a small local model, a disk cache, and rank fusion with the lexical score.

The model runs on the CPU through ``fastembed`` (``requirements-embeddings.txt``), so no API key is
needed. Without the package this module still imports: ``available()`` is False and only vectors
already in a cache can be read, which is how the offline eval runs from a committed cache.

Cache file: one JSON header line ``{"model", "dim", "dtype": "int8"}``, then fixed-size records of a
32-byte sha256 of ``model + "\\n" + text`` followed by ``dim`` signed bytes. Each vector is scaled so
its largest component is +-127 and rounded; cosine ignores scale, so the scale is not stored. At 384
dims a record is 416 bytes, about 2.1 MB for 5,000 texts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import tempfile
from array import array
from operator import mul
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from ...utils.paths import data_dir

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384
CACHE_FILENAME = "embeddings_cache.bin"
RRF_K = 60

Embedder = Callable[[list[str]], Sequence[Sequence[float]]]

_models: dict[str, object] = {}


def available() -> bool:
    """True when fastembed is installed, so texts not in the cache can be embedded."""
    return importlib.util.find_spec("fastembed") is not None


def embed_with_model(
    texts: list[str], model_name: str = MODEL_NAME, local_files_only: bool = False
) -> list[list[float]]:
    """Embed ``texts`` with the local model (loaded once per process, downloaded on first use)."""
    if not available():
        raise RuntimeError("fastembed is not installed: pip install -r requirements-embeddings.txt")
    model = _models.get(model_name)
    if model is None:
        from fastembed import TextEmbedding

        model = TextEmbedding(model_name, local_files_only=local_files_only)
        _models[model_name] = model
    return [[float(x) for x in vector] for vector in model.embed(texts)]


def quantize(vector: Sequence[float]) -> bytes:
    peak = max((abs(x) for x in vector), default=0.0)
    if peak == 0.0:
        return bytes(len(vector))
    return array("b", (round(x / peak * 127) for x in vector)).tobytes()


def dequantize(raw: bytes) -> list[float]:
    return [value / 127 for value in array("b", raw)]


class EmbeddingCache:
    """int8 vectors on disk, keyed by sha256(model + text)."""

    def __init__(self, path: Path, model_name: str = MODEL_NAME, dim: int = DIM) -> None:
        self.path = Path(path)
        self.model_name = model_name
        self.dim = dim
        self._rows: dict[bytes, bytes] = {}
        self._dirty = False
        if self.path.exists():
            self._load()

    def key(self, text: str) -> bytes:
        return hashlib.sha256(f"{self.model_name}\n{text}".encode("utf-8")).digest()

    def _header(self) -> bytes:
        return json.dumps({"model": self.model_name, "dim": self.dim, "dtype": "int8"}).encode() + b"\n"

    def _load(self) -> None:
        data = self.path.read_bytes()
        split = data.index(b"\n") + 1
        if data[:split] != self._header():
            raise ValueError(f"{self.path} was written for another model or format: {data[:split]!r}")
        size = 32 + self.dim
        body = data[split:]
        if len(body) % size:
            raise ValueError(f"{self.path} is truncated")
        for start in range(0, len(body), size):
            self._rows[body[start : start + 32]] = body[start + 32 : start + size]

    def __contains__(self, text: str) -> bool:
        return self.key(text) in self._rows

    def __len__(self) -> int:
        return len(self._rows)

    def get(self, text: str) -> list[float] | None:
        raw = self._rows.get(self.key(text))
        return dequantize(raw) if raw is not None else None

    def put(self, text: str, vector: Sequence[float]) -> None:
        if len(vector) != self.dim:
            raise ValueError(f"expected {self.dim} dims, got {len(vector)}")
        self._rows[self.key(text)] = quantize(vector)
        self._dirty = True

    def retain(self, texts: Iterable[str]) -> None:
        """Drop every entry whose text is not in ``texts``."""
        keep = {self.key(text) for text in texts}
        for key in [key for key in self._rows if key not in keep]:
            del self._rows[key]
            self._dirty = True

    def save(self) -> None:
        """Write atomically, records sorted by key so the same entries always give the same bytes."""
        if not self._dirty and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._header() + b"".join(key + self._rows[key] for key in sorted(self._rows))
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._dirty = False


def embed_cached(
    texts: Sequence[str], cache: EmbeddingCache, embedder: Embedder | None = None
) -> list[list[float]]:
    """Vectors for ``texts``; only texts missing from ``cache`` reach the embedder.

    Every vector comes back through int8, so a fresh embed and a later cache hit are identical.
    """
    missing = [text for text in dict.fromkeys(texts) if text not in cache]
    if missing:
        fresh = embedder(missing) if embedder else embed_with_model(missing, cache.model_name)
        for text, vector in zip(missing, fresh):
            cache.put(text, vector)
    return [cache.get(text) for text in texts]  # type: ignore[misc]


def embed(texts: Sequence[str], cache_dir: Path | None = None) -> list[list[float]]:
    """Embed ``texts`` through the cache in ``cache_dir`` (default: the data dir) and save it."""
    cache = EmbeddingCache(Path(cache_dir or data_dir() / "embeddings") / CACHE_FILENAME)
    vectors = embed_cached(texts, cache)
    cache.save()
    return vectors


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.hypot(*a) * math.hypot(*b)
    return sum(map(mul, a, b)) / norm if norm else 0.0


def rrf(rankings: Sequence[Sequence[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion: each ranking adds 1 / (k + rank) for every id it lists (rank from 1)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return scores


def hybrid_scores(
    lexical_order: Sequence[str],
    query_vector: Sequence[float],
    vectors: Mapping[str, Sequence[float]],
    k: int = RRF_K,
) -> dict[str, float]:
    """Fuse the lexical ranking with the cosine ranking; an id with no vector keeps its lexical share."""
    similarity = {aid: cosine(query_vector, vectors[aid]) for aid in lexical_order if aid in vectors}
    semantic_order = sorted(similarity, key=lambda aid: (-similarity[aid], aid))
    return rrf([lexical_order, semantic_order], k)
