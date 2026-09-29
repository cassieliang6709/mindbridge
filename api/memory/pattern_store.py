"""Pending reflective inferences, kept outside T3 until user confirmation.

中文说明：反思型推断先作为 Pattern Candidate 保存，只有用户明确确认后才可进入
T3；拒绝的候选仍保留审核记录，但不会变成长期记忆。
"""

from __future__ import annotations

import json

import asyncpg

from ..models import PatternCandidate, PatternCandidateCreate, PatternStatus

_COLUMNS = """
    id, description, supporting_evidence, counter_evidence, contexts,
    confidence, status, resolution_note, confirmed_memory_id, created_at, updated_at
"""


def _as_candidate(row: asyncpg.Record) -> PatternCandidate:
    """Decode one database row at the storage boundary.

    中文：在存储边界把 asyncpg 的 JSON 文本解码为 Pydantic 所需的列表。

    Args:
        row: Record returned from the pattern_candidates table.

    Returns:
        A validated PatternCandidate.
    """
    payload = dict(row)
    # asyncpg returns json/jsonb as text unless a custom codec is installed.
    # Decode at this store boundary so the Pydantic model always sees lists.
    # 中文：若未注册自定义 codec，asyncpg 会返回 JSON 字符串；统一在这里解码。
    for field in ("supporting_evidence", "counter_evidence", "contexts"):
        value = payload[field]
        if isinstance(value, str):
            payload[field] = json.loads(value)
    return PatternCandidate.model_validate(payload)


class PatternCandidateStore:
    """Persist the review lifecycle for reflective Pattern Candidates.

    中文：持久化反思型 Pattern Candidate 的创建、读取与审核状态变化。
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Create the store around an open PostgreSQL connection pool.

        中文：使用已打开的 PostgreSQL 连接池创建候选存储。
        """
        self._pool = pool

    async def create(self, draft: PatternCandidateCreate) -> PatternCandidate:
        """Insert a pending candidate with its inspectable evidence.

        中文：插入一条待审核候选及其可检查的支持/反证证据。

        Args:
            draft: Validated candidate proposal.

        Returns:
            The stored pending candidate.
        """
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
        """Read one candidate by id, or None when it does not exist.

        中文：按 ID 读取候选；不存在时返回 None。
        """
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
        """List newest candidates, optionally filtered by lifecycle status.

        中文：按最新优先列出候选，并可按生命周期状态过滤。
        """
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
        """Resolve one still-pending candidate exactly once.

        中文：对仍处于 pending 状态的候选执行一次最终审核决定。

        Raises:
            KeyError: If the candidate is missing or was already resolved.
        """
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
