"""Regression tests for the reflective review surface."""

import json
import unittest
from datetime import datetime, timezone

from api.memory.pattern_store import _as_candidate
from api.models import MemoryWithDecay
from mcp_server.server import _format_memory, _format_pattern


class PatternLoopFormattingTests(unittest.TestCase):
    def test_asyncpg_json_text_is_decoded_at_store_boundary(self) -> None:
        now = datetime.now(timezone.utc)
        candidate = _as_candidate(  # type: ignore[arg-type]
            {
                "id": 7,
                "description": "A synthetic repeated working pattern.",
                "supporting_evidence": json.dumps(
                    [
                        {"source_date": "2026-08-17", "summary": "Event one"},
                        {"source_date": "2026-08-18", "summary": "Event two"},
                        {"source_date": "2026-08-19", "summary": "Event three"},
                    ]
                ),
                "counter_evidence": "[]",
                "contexts": '["synthetic"]',
                "confidence": 0.75,
                "status": "pending",
                "resolution_note": None,
                "confirmed_memory_id": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        self.assertEqual(candidate.contexts, ["synthetic"])
        self.assertEqual(len(candidate.supporting_evidence), 3)
        self.assertIn("Pattern Candidate [7]", _format_pattern(candidate))

    def test_memory_formatter_returns_traceable_namespace(self) -> None:
        now = datetime.now(timezone.utc)
        memory = MemoryWithDecay(
            id=12,
            content="A user-confirmed synthetic pattern.",
            namespace="reflective",
            category="confirmed_pattern",
            created_at=now,
            valid_at=None,
            superseded_by=None,
            access_count=0,
            decay_factor=1.0,
            age_days=0.0,
            decay_multiplier=1.0,
        )
        rendered = _format_memory(memory)
        self.assertIn("[12]", rendered)
        self.assertIn("namespace=reflective", rendered)


if __name__ == "__main__":
    unittest.main()
