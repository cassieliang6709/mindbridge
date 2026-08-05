"""Embedding providers.

Two hosted providers plus a deterministic local fallback. The fallback exists
so the stack boots, the schema applies and the evals run without an API key —
it is a hashing bag-of-words, so it measures lexical overlap, not meaning.
Anything it produces is labelled as such in results.json.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

from .settings import Settings

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[一-鿿]")


class Embedder(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    if norm == 0.0:
        # A zero vector has undefined cosine similarity; nudge it so pgvector
        # returns a defined distance instead of NaN.
        return [1.0] + [0.0] * (len(vector) - 1)
    return [component / norm for component in vector]


class HashingEmbedder:
    """Deterministic, offline, lexical-only. Not a semantic model.

    Tokens are hashed into buckets with a signed weight, which makes cosine
    similarity approximate token overlap. Good enough to exercise the dedup and
    decay code paths; useless for judging retrieval quality.
    """

    name = "hashing-local"

    def __init__(self, dim: int) -> None:
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        return _l2_normalise(vector)


class OpenAIEmbedder:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise ValueError(
                "MINDBRIDGE_EMBEDDING_PROVIDER=openai requires "
                "MINDBRIDGE_OPENAI_API_KEY"
            )
        self.dim = settings.embedding_dim
        self._model = settings.embedding_model
        self._key = settings.openai_api_key
        self._timeout = settings.embedding_timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={"Authorization": f"Bearer {self._key}"},
                json={"model": self._model, "input": texts, "dimensions": self.dim},
            )
            response.raise_for_status()
            payload = response.json()
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]


class GeminiEmbedder:
    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        if not settings.gemini_api_key:
            raise ValueError(
                "MINDBRIDGE_EMBEDDING_PROVIDER=gemini requires "
                "MINDBRIDGE_GEMINI_API_KEY"
            )
        self.dim = settings.embedding_dim
        self._model = settings.embedding_model
        self._key = settings.gemini_api_key
        self._timeout = settings.embedding_timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self._model}:batchEmbedContents"
        )
        requests = [
            {
                "model": f"models/{self._model}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.dim,
            }
            for text in texts
        ]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                headers={"x-goog-api-key": self._key},
                json={"requests": requests},
            )
            response.raise_for_status()
            payload = response.json()
        return [item["values"] for item in payload["embeddings"]]


class OllamaEmbedder:
    """Local semantic embeddings through ollama.

    The reason this exists: the hashing fallback measures token overlap, so it
    scores real duplicates 0.13-0.73 — "Use uv instead of pip" against
    "Python 项目优先用 uv" lands at 0.25 because they share almost no tokens.
    No threshold separates duplicates from unrelated preferences, so write-time
    dedup silently never fires and T3 fills with paraphrases of one fact.

    Measured on the same pairs, nomic-embed-text scores duplicates 0.67-0.86 and
    unrelated preferences 0.34-0.44, which a threshold around 0.62 separates.

    Unlike the hosted embedders this keeps the local-only promise intact, which
    matters because embeddings are computed on every preference write.
    """

    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.dim = settings.embedding_dim
        self._model = settings.embedding_model
        self._url = str(settings.ollama_url).rstrip("/")
        self._timeout = settings.embedding_timeout_seconds

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._url}/api/embed",
                json={"model": self._model, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
        vectors = payload["embeddings"]
        if vectors and len(vectors[0]) != self.dim:
            raise ValueError(
                f"{self._model} returned {len(vectors[0])}-dim vectors but "
                f"MINDBRIDGE_EMBEDDING_DIM is {self.dim}"
            )
        # Normalised so pgvector's cosine distance and a plain dot product agree.
        return [_l2_normalise(vector) for vector in vectors]


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_provider == "ollama":
        return OllamaEmbedder(settings)
    if settings.embedding_provider == "openai":
        return OpenAIEmbedder(settings)
    if settings.embedding_provider == "gemini":
        return GeminiEmbedder(settings)
    return HashingEmbedder(settings.embedding_dim)


def to_pgvector(vector: list[float]) -> str:
    """asyncpg has no native pgvector codec; the text literal is the contract."""
    return "[" + ",".join(f"{component:.8f}" for component in vector) + "]"
