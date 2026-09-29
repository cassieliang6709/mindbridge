"""Unit coverage for Night Shift identities and review boundaries."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from api.background_jobs import _as_job, canonical_job_key
from api.memory_candidates import (
    MemoryCandidateCreate,
    MemoryCandidateDecisionRequest,
    _as_candidate,
    candidate_key,
)


class BackgroundJobIdentityTests(unittest.TestCase):
    def test_job_key_ignores_payload_order_but_not_job_kind(self) -> None:
        first = canonical_job_key("extract_card", {"period": "2026-09-02", "limit": 3})
        second = canonical_job_key("extract_card", {"limit": 3, "period": "2026-09-02"})
        evaluation = canonical_job_key("retrieval_eval", {"limit": 3, "period": "2026-09-02"})
        self.assertEqual(first, second)
        self.assertNotEqual(first, evaluation)

    def test_asyncpg_json_text_is_decoded_at_job_boundary(self) -> None:
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        job = _as_job(  # type: ignore[arg-type]
            {
                "job_id": "job_test",
                "kind": "extract_card",
                "idempotency_key": "key",
                "status": "queued",
                "payload": '{"period":"2026-09-02"}',
                "result": '{"candidate_ids":[7]}',
                "attempt": 0,
                "max_attempts": 3,
                "celery_task_id": None,
                "error_code": None,
                "error_message": None,
                "created_at": now,
                "updated_at": now,
                "started_at": None,
                "heartbeat_at": None,
                "finished_at": None,
            }
        )
        self.assertEqual(job.payload["period"], "2026-09-02")
        self.assertEqual(job.result, {"candidate_ids": [7]})


class MemoryCandidateTests(unittest.TestCase):
    def test_candidate_key_is_stable_and_scoped_to_source_card(self) -> None:
        kwargs = {
            "content": "Prefers concise implementation plans.",
            "category": "coding_style",
            "project": "MindBridge",
        }
        first = candidate_key(source_summary_id=7, **kwargs)
        duplicate = candidate_key(
            source_summary_id=7,
            content="  prefers concise  implementation plans. ",
            category="coding_style",
            project="MindBridge",
        )
        next_card = candidate_key(source_summary_id=8, **kwargs)
        self.assertEqual(first, duplicate)
        self.assertNotEqual(first, next_card)

    def test_operational_candidate_cannot_claim_reflective_category(self) -> None:
        with self.assertRaises(ValidationError):
            MemoryCandidateCreate(
                content="A repeated identity-level inference.",
                namespace="operational",
                category="identity_hypothesis",
                confidence=0.9,
                source_period="2026-09-02",
            )

    def test_edit_requires_user_approved_wording(self) -> None:
        with self.assertRaises(ValidationError):
            MemoryCandidateDecisionRequest(decision="edit")

    def test_candidate_receipt_is_decoded_at_store_boundary(self) -> None:
        now = datetime(2026, 9, 2, tzinfo=timezone.utc)
        candidate = _as_candidate(  # type: ignore[arg-type]
            {
                "id": 7,
                "content": "Prefers source-backed resume claims.",
                "namespace": "operational",
                "category": "tool_preference",
                "project": None,
                "confidence": 0.88,
                "evidence": "The user rejected unsupported claims.",
                "source_period": "2026-09-02",
                "source_session_id": "session-1",
                "source_summary_id": 12,
                "source_receipt": json.dumps({"model": "local", "attempts": 1}),
                "dedupe_key": "key",
                "status": "pending",
                "resolution_note": None,
                "confirmed_memory_id": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        self.assertEqual(candidate.source_receipt["model"], "local")


if __name__ == "__main__":
    unittest.main()
