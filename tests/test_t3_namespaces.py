"""Safety boundary between operational and reflective T3 memory."""

import unittest
from datetime import date

from pydantic import ValidationError

from api.models import (
    PatternCandidateCreate,
    PatternDecisionRequest,
    PatternEvidence,
    TemporalQueryRequest,
    UpsertPreferenceRequest,
)


class T3NamespaceTests(unittest.TestCase):
    def test_existing_writes_default_to_operational(self) -> None:
        request = UpsertPreferenceRequest(
            content="Prefer uv for Python projects",
            category="tool_preference",
        )
        self.assertEqual(request.namespace, "operational")

    def test_reflective_memory_requires_user_confirmation(self) -> None:
        with self.assertRaisesRegex(ValidationError, "explicit user confirmation"):
            UpsertPreferenceRequest(
                content=(
                    "When scope is unclear, I tend to add features before narrowing it"
                ),
                namespace="reflective",
                category="confirmed_pattern",
            )

    def test_confirmed_reflective_memory_is_accepted(self) -> None:
        request = UpsertPreferenceRequest(
            content="When scope is unclear, I tend to add features before narrowing it",
            namespace="reflective",
            category="confirmed_pattern",
            confirmed_by_user=True,
        )
        self.assertEqual(request.namespace, "reflective")

    def test_categories_cannot_cross_namespaces(self) -> None:
        cases = [
            ("operational", "identity_hypothesis"),
            ("reflective", "tool_preference"),
        ]
        for namespace, category in cases:
            with self.subTest(namespace=namespace, category=category):
                with self.assertRaisesRegex(ValidationError, "category"):
                    UpsertPreferenceRequest(
                        content="A durable statement",
                        namespace=namespace,
                        category=category,
                        confirmed_by_user=True,
                    )

    def test_query_can_select_one_t3_lane(self) -> None:
        request = TemporalQueryRequest(
            query_string="How do I usually respond to unclear scope?",
            namespaces=["reflective"],
        )
        self.assertEqual(request.namespaces, ["reflective"])

    def test_pattern_candidate_needs_three_observations_across_two_dates(self) -> None:
        one = PatternEvidence(source_date=date(2026, 8, 18), summary="First event")
        with self.assertRaisesRegex(ValidationError, "at least 3"):
            PatternCandidateCreate(
                description="I may broaden scope when a project feels uncertain",
                supporting_evidence=[one],
                contexts=["personal projects"],
                confidence=0.6,
            )

        with self.assertRaisesRegex(ValidationError, "at least two dates"):
            PatternCandidateCreate(
                description="I may broaden scope when a project feels uncertain",
                supporting_evidence=[one, one, one],
                contexts=["personal projects"],
                confidence=0.6,
            )

    def test_edit_decision_needs_user_wording(self) -> None:
        with self.assertRaisesRegex(ValidationError, "confirmed_content"):
            PatternDecisionRequest(decision="edit")


if __name__ == "__main__":
    unittest.main()
