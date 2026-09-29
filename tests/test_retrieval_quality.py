"""Unit coverage for source-labelled retrieval quality scoring."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from pydantic import ValidationError

from api.models import MemoryHit
from evals.retrieval_quality import (
    LabelProvenance,
    RetrievalCase,
    RetrievalJudgement,
    aggregate_metrics,
    evaluate,
    score_case,
)


def _hit(memory_id: int) -> MemoryHit:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return MemoryHit(
        id=memory_id,
        content=f"memory {memory_id}",
        namespace="operational",
        category="other",
        created_at=now,
        valid_at=None,
        superseded_by=None,
        access_count=0,
        decay_factor=1.0,
        project=None,
        cosine_similarity=0.8,
        age_days=0,
        decay_multiplier=1.0,
        score=0.8,
    )


def _case(judgement: RetrievalJudgement) -> RetrievalCase:
    return RetrievalCase(
        case_id="case-1",
        query="what does the user prefer",
        provenance=LabelProvenance(
            source_type="manual",
            source_ref="tests/test_retrieval_quality.py",
            captured_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ),
        judgement=judgement,
    )


class RetrievalQualityTests(unittest.TestCase):
    def test_draft_case_never_produces_metrics(self) -> None:
        result = score_case(_case(RetrievalJudgement()), [_hit(10)])

        self.assertIsNone(result["judgement_coverage_at_k"])
        self.assertIsNone(result["precision_at_k"])
        self.assertIsNone(result["reciprocal_rank"])

    def test_confirmed_candidate_pool_scores_precision_and_rank(self) -> None:
        judgement = RetrievalJudgement(
            status="confirmed",
            judged_memory_ids=[99, 20, 10, 20],
            relevant_memory_ids=[20, 10, 20],
            labelled_by="Cassie",
            labelled_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        result = score_case(_case(judgement), [_hit(99), _hit(10)])

        self.assertTrue(result["has_relevant_hit"])
        self.assertEqual(result["relevant_memory_ids"], [10, 20])
        self.assertEqual(result["judgement_coverage_at_k"], 1.0)
        self.assertEqual(result["precision_at_k"], 0.5)
        self.assertEqual(result["reciprocal_rank"], 0.5)

    def test_confirmed_label_requires_receipt_and_candidate_pool(self) -> None:
        with self.assertRaises(ValidationError):
            RetrievalJudgement(status="confirmed")

    def test_unseen_candidates_make_quality_metrics_null(self) -> None:
        judgement = RetrievalJudgement(
            status="confirmed",
            judged_memory_ids=[10],
            relevant_memory_ids=[],
            labelled_by="Cassie",
            labelled_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )

        result = score_case(_case(judgement), [_hit(10), _hit(20)])

        self.assertEqual(result["judgement_coverage_at_k"], 0.5)
        self.assertIsNone(result["precision_at_k"])
        self.assertIsNone(result["reciprocal_rank"])

    def test_aggregate_excludes_drafts(self) -> None:
        metrics = aggregate_metrics(
            [
                {
                    "status": "draft",
                    "judgement_coverage_at_k": None,
                    "has_relevant_hit": None,
                    "precision_at_k": None,
                    "reciprocal_rank": None,
                },
                {
                    "status": "confirmed",
                    "judgement_coverage_at_k": 1.0,
                    "has_relevant_hit": True,
                    "precision_at_k": 0.5,
                    "reciprocal_rank": 1.0,
                },
            ]
        )

        self.assertEqual(metrics["confirmed_cases"], 1)
        self.assertEqual(metrics["draft_cases"], 1)
        self.assertEqual(metrics["hit_rate_at_k"], 1.0)
        self.assertEqual(metrics["precision_at_k"], 0.5)


class _FakeEmbedder:
    name = "fake-embedder"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class _FakeVectors:
    def __init__(self) -> None:
        self.search_calls: list[dict[str, object]] = []

    async def search(self, embedding: list[float], **kwargs: object) -> list[MemoryHit]:
        self.search_calls.append({"embedding": embedding, **kwargs})
        return [_hit(10)]

    async def count(self) -> int:
        return 1


class RetrievalQualityEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_override_passes_semantic_mode_and_records_receipt(self) -> None:
        vectors = _FakeVectors()
        service = SimpleNamespace(
            embedder=_FakeEmbedder(),
            vectors=vectors,
            settings=SimpleNamespace(decay_rate_per_day=0.01),
        )
        dataset = SimpleNamespace(
            name="test",
            schema_version="retrieval-quality.v1",
            cases=[_case(RetrievalJudgement())],
        )

        result = await evaluate(
            dataset,
            "dataset-sha",
            service=service,
            ranking_mode="semantic",
        )

        self.assertEqual(vectors.search_calls[0]["ranking_mode"], "semantic")
        self.assertFalse(vectors.search_calls[0]["record_access"])
        self.assertEqual(result["retrieval"]["ranking_mode"], "semantic")
        self.assertEqual(result["retrieval"]["implementation"], "pgvector_cosine")
        self.assertEqual(result["cases"][0]["ranking_mode"], "semantic")


if __name__ == "__main__":
    unittest.main()
