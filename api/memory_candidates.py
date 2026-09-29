"""Review queue for model-proposed long-term memory."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    MemoryCategory,
    MemoryNamespace,
    OPERATIONAL_CATEGORIES,
    REFLECTIVE_CATEGORIES,
)

MemoryCandidateStatus = Literal["pending", "confirmed", "edited", "rejected"]
MemoryCandidateDecision = Literal["confirm", "edit", "reject"]

_COLUMNS = """
    id, content, namespace, category, project, confidence, evidence, source_period,
    source_session_id, source_summary_id, source_receipt, dedupe_key, status,
    resolution_note, confirmed_memory_id, created_at, updated_at
"""


def candidate_key(
    *, source_summary_id: int | None, content: str, category: str, project: str | None
) -> str:
    canonical = json.dumps(
        {
            "source_summary_id": source_summary_id,
            "content": " ".join(content.split()).lower(),
            "category": category,
            "project": project,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryCandidateCreate(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    namespace: MemoryNamespace = "operational"
    category: MemoryCategory = "other"
    project: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: str | None = Field(default=None, max_length=1000)
    source_period: str
    source_session_id: str | None = None
    source_summary_id: int | None = None
    source_receipt: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_lane(self) -> "MemoryCandidateCreate":
        allowed = (
            OPERATIONAL_CATEGORIES
            if self.namespace == "operational"
            else REFLECTIVE_CATEGORIES
        )
        if self.category not in allowed:
            raise ValueError(f"{self.namespace} candidate has an incompatible category")
        return self


class MemoryCandidate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    namespace: MemoryNamespace
    category: MemoryCategory
    project: str | None
    confidence: float
    evidence: str | None
    source_period: str
    source_session_id: str | None
    source_summary_id: int | None
    source_receipt: dict[str, object]
    dedupe_key: str
    status: MemoryCandidateStatus
    resolution_note: str | None
    confirmed_memory_id: int | None
    created_at: datetime
    updated_at: datetime


class MemoryCandidateDecisionRequest(BaseModel):
    decision: MemoryCandidateDecision
    confirmed_content: str | None = Field(default=None, min_length=1, max_length=1000)
    resolution_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_edit_text(self) -> "MemoryCandidateDecisionRequest":
        if self.decision == "edit" and not self.confirmed_content:
            raise ValueError("edit requires confirmed_content")
        return self


def _as_candidate(row: asyncpg.Record | Mapping[str, object]) -> MemoryCandidate:
    payload = dict(row)
    receipt = payload.get("source_receipt")
    if isinstance(receipt, str):
        receipt = json.loads(receipt)
    payload["source_receipt"] = dict(receipt or {})
    return MemoryCandidate.model_validate(payload)


class MemoryCandidateStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, draft: MemoryCandidateCreate) -> MemoryCandidate:
        key = candidate_key(
            source_summary_id=draft.source_summary_id,
            content=draft.content,
            category=draft.category,
            project=draft.project,
        )
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO memory_candidates
                (content, namespace, category, project, confidence, evidence, source_period,
                 source_session_id, source_summary_id, source_receipt, dedupe_key)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)
            ON CONFLICT (dedupe_key) DO UPDATE
            SET dedupe_key = EXCLUDED.dedupe_key
            RETURNING {_COLUMNS}
            """,
            draft.content.strip(),
            draft.namespace,
            draft.category,
            draft.project,
            draft.confidence,
            draft.evidence,
            draft.source_period,
            draft.source_session_id,
            draft.source_summary_id,
            json.dumps(draft.source_receipt, ensure_ascii=False),
            key,
        )
        assert row is not None
        return _as_candidate(row)

    async def get(self, candidate_id: int) -> MemoryCandidate | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM memory_candidates WHERE id = $1", candidate_id
        )
        return _as_candidate(row) if row is not None else None

    async def list(
        self, *, status: MemoryCandidateStatus | None = "pending", limit: int = 50
    ) -> list[MemoryCandidate]:
        rows = await self._pool.fetch(
            f"""
            SELECT {_COLUMNS} FROM memory_candidates
            WHERE ($1::text IS NULL OR status = $1)
            ORDER BY created_at DESC, id DESC LIMIT $2
            """,
            status,
            limit,
        )
        return [_as_candidate(row) for row in rows]

    async def resolve(
        self,
        candidate_id: int,
        *,
        status: MemoryCandidateStatus,
        content: str,
        resolution_note: str | None,
        confirmed_memory_id: int | None,
    ) -> MemoryCandidate:
        row = await self._pool.fetchrow(
            f"""
            UPDATE memory_candidates
            SET status = $2, content = $3, resolution_note = $4,
                confirmed_memory_id = $5, updated_at = now()
            WHERE id = $1 AND status = 'pending'
            RETURNING {_COLUMNS}
            """,
            candidate_id,
            status,
            content.strip(),
            resolution_note,
            confirmed_memory_id,
        )
        if row is None:
            raise KeyError(f"memory candidate {candidate_id} not found or resolved")
        return _as_candidate(row)
