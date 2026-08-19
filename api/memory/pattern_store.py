"""Pending reflective inferences, kept outside T3 until user confirmation."""

from __future__ import annotations

import json

import asyncpg

from ..models import PatternCandidate, PatternCandidateCreate, PatternStatus

_COLUMNS = """
    id, description, supporting_evidence, counter_evidence, contexts,
    confidence, status, resolution_note, confirmed_memory_id, created_at, updated_at
"""


def _as_candidate(row: asyncpg.Record) -> PatternCandidate:
    payload = dict(row)
    # asyncpg returns json/jsonb as text unless a custom codec is installed.
    # Decode at this store boundary so the Pydantic model always sees lists.
    for field in ("supporting_evidence", "counter_evidence", "contexts"):
        value = payload[field]
        if isinstance(value, str):
            payload[field] = json.loads(value)
    return PatternCandidate.model_validate(payload)


class PatternCandidateStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, draft: PatternCandidateCreate) -> PatternCandidate:
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO pattern_candidates
                (description, supporting_evidence, counter_evidence, contexts, confidence)
            VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, $5)
            RETURNING {_COLUMNS}
            """,
            draft.description.strip(),
            json.dumps([item.model_dump(mode="json") for item in draft.supporting_evidence]),
            json.dumps([item.model_dump(mode="json") for item in draft.counter_evidence]),
            json.dumps(draft.contexts),
            draft.confidence,
        )
        assert row is not None
        return _as_candidate(row)

    async def get(self, candidate_id: int) -> PatternCandidate | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM pattern_candidates WHERE id = $1",
            candidate_id,
        )
        return _as_candidate(row) if row is not None else None

    async def list(
        self,
        *,
        status: PatternStatus | None = "pending",
        limit: int = 20,
    ) -> list[PatternCandidate]:
        rows = await self._pool.fetch(
            f"""
            SELECT {_COLUMNS}
            FROM pattern_candidates
            WHERE ($1::text IS NULL OR status = $1)
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """,
            status,
            limit,
        )
        return [_as_candidate(row) for row in rows]

    async def resolve(
        self,
        candidate_id: int,
        *,
        status: PatternStatus,
        description: str,
        resolution_note: str | None,
        confirmed_memory_id: int | None,
    ) -> PatternCandidate:
        row = await self._pool.fetchrow(
            f"""
            UPDATE pattern_candidates
            SET status = $2,
                description = $3,
                resolution_note = $4,
                confirmed_memory_id = $5,
                updated_at = now()
            WHERE id = $1 AND status = 'pending'
            RETURNING {_COLUMNS}
            """,
            candidate_id,
            status,
            description.strip(),
            resolution_note,
            confirmed_memory_id,
        )
        if row is None:
            raise KeyError(f"pattern candidate {candidate_id} not found or already resolved")
        return _as_candidate(row)
