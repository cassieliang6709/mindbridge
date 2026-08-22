"""Regression coverage for project-aware temporal queries."""

import unittest
from types import SimpleNamespace

from api.cache import SemanticLookup
from api.models import TemporalQueryRequest
from api.service import MemoryService


class _FakeEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _FakeCache:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.fingerprints: list[str] = []

    async def get(self, key: str) -> None:
        self.keys.append(key)
        return None

    async def get_semantic(
        self, namespace: str, fingerprint: str, embedding: list[float]
    ) -> SemanticLookup:
        self.fingerprints.append(fingerprint)
        return SemanticLookup(None, None, None, None, 0, "disabled")

    async def set(self, key: str, value: dict[str, object]) -> None:
        return None

    async def index_semantic(
        self,
        namespace: str,
        key: str,
        query: str,
        fingerprint: str,
        embedding: list[float],
    ) -> None:
        return None


class _FakeVectors:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []

    async def search(self, embedding: list[float], **kwargs: object) -> list[object]:
        self.search_calls.append(kwargs)
        return []


class TemporalProjectTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_partitions_cache_and_vector_search(self) -> None:
        cache = _FakeCache()
        vectors = _FakeVectors()
        service = object.__new__(MemoryService)
        service.cache = cache
        service.embedder = _FakeEmbedder()
        service.vectors = vectors
        service.settings = SimpleNamespace(decay_rate_per_day=0.01)

        await service.temporal_query(
            TemporalQueryRequest(query_string="How should I write?", project="alpha")
        )
        await service.temporal_query(
            TemporalQueryRequest(query_string="How should I write?", project="beta")
        )

        self.assertEqual(len(cache.keys), 2)
        self.assertNotEqual(cache.keys[0], cache.keys[1])
        self.assertEqual(len(cache.fingerprints), 2)
        self.assertNotEqual(cache.fingerprints[0], cache.fingerprints[1])
        self.assertEqual(
            [call["project"] for call in vectors.search_calls], ["alpha", "beta"]
        )

    def test_project_is_optional_and_defaults_to_no_downweighting(self) -> None:
        self.assertIsNone(TemporalQueryRequest(query_string="same query").project)


if __name__ == "__main__":
    unittest.main()
