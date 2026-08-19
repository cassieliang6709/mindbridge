"""Unit tests for deterministic T2 pattern suggestion helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import unittest

from scripts.suggest_patterns import (
    _DATE_RE,
    make_candidates,
    parse_period_for_sort,
    parse_since,
    _normalise,
)


@dataclass
class FakeCard:
    id: int
    period: str
    developer_behavior_facts: list[str]


class PatternSuggestorTests(unittest.TestCase):
    def test_parse_since_parses_hours_days_and_weeks(self) -> None:
        before = datetime.now(timezone.utc)
        threshold = parse_since("48h")
        after = datetime.now(timezone.utc)
        assert isinstance(threshold, datetime)
        assert before - timedelta(hours=48, seconds=2) <= threshold <= after

        with self.assertRaises(ValueError):
            parse_since("5m")

    def test_parse_period_for_sort(self) -> None:
        assert parse_period_for_sort("2026-08-20") is not None
        assert parse_period_for_sort("2026-08") is None
        assert not _DATE_RE.match("2026-8-20")

    def test_normalise_strips_non_alpha(self) -> None:
        assert _normalise("Tool-Calls ×5") == "tool calls 5"

    def test_make_candidates_gates_by_observations_and_dates(self) -> None:
        cards = [
            FakeCard(
                id=1,
                period="2026-08-01",
                developer_behavior_facts=[
                    "Projects touched: Alpha",
                ],
            ),
            FakeCard(
                id=2,
                period="2026-08-02",
                developer_behavior_facts=[
                    "Projects touched: Alpha",
                ],
            ),
            FakeCard(
                id=3,
                period="2026-08-03",
                developer_behavior_facts=[
                    "Projects touched: Alpha",
                ],
            ),
        ]

        candidates = make_candidates(
            cards,
            min_observations=3,
            min_dates=2,
        )
        assert candidates
        assert candidates[0].supporting_evidence
        assert len(candidates[0].supporting_evidence) == 3
        assert "project" in candidates[0].description

    def test_make_candidates_requires_two_dates(self) -> None:
        cards = [
            FakeCard(
                id=10,
                period="2026-08-01",
                developer_behavior_facts=["Tool calls: rg ×3"],
            ),
            FakeCard(
                id=11,
                period="2026-08-01",
                developer_behavior_facts=["Tool calls: rg ×2"],
            ),
            FakeCard(
                id=12,
                period="2026-08-01",
                developer_behavior_facts=["Tool calls: rg ×1"],
            ),
        ]
        candidates = make_candidates(
            cards,
            min_observations=3,
            min_dates=2,
        )
        assert candidates == []


if __name__ == "__main__":
    unittest.main()
