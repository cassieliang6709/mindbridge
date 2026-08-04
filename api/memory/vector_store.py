"""T3 — long-term vector memory with time decay and write-time dedup.

Retrieval score:

    score = cosine_similarity * exp(-decay_rate * decay_factor * Δt_days)

Cosine comes from pgvector's `<=>` operator (cosine *distance*, so similarity
is `1 - distance`). Δt is measured from `created_at`. `decay_factor` is a
per-record multiplier, so a record can be pinned (small factor) or made
deliberately volatile (large factor) without changing the global rate.

Superseded records are not deleted. `valid_at` is stamped with the moment the
fact stopped being true and `superseded_by` points at the replacement, so the
history stays queryable and an audit can show what the user used to prefer.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from ..embeddings import to_pgvector
from ..models import (
    MemoryCategory,
    MemoryHit,
    MemoryRecord,
    UpsertAction,
)

_RECORD_COLUMNS = """
    id, content, category, created_at, valid_at, superseded_by,
    access_count, decay_factor
"""


@dataclass(slots=True)
class NearestMatch:
    record: MemoryRecord
    similarity: float


class VectorMemoryStore:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        decay_rate_per_day: float,
        dedup_threshold: float,
        superseded_penalty: float,
    ) -> None:
        self._pool = pool
        self._decay_rate = decay_rate_per_day
        self._dedup_threshold = dedup_threshold
        self._superseded_penalty = superseded_penalty

    @property
    def decay_rate_per_day(self) -> float:
        return self._decay_rate

    @property
    def dedup_threshold(self) -> float:
        return self._dedup_threshold

    # --- writes -----------------------------------------------------------

    async def nearest_open(
        self,
        embedding: list[float],
        category: MemoryCategory | None = None,
    ) -> NearestMatch | None:
        """Closest still-valid record, for the dedup decision."""
        row = await self._pool.fetchrow(
            f"""
            SELECT {_RECORD_COLUMNS},
                   1 - (embedding <=> $1::vector) AS similarity
            FROM memory_vectors
            WHERE valid_at IS NULL
              AND ($2::text IS NULL OR category = $2)
            ORDER BY embedding <=> $1::vector
            LIMIT 1
            """,
            to_pgvector(embedding),
            category,
        )
        if row is None:
            return None
        data = dict(row)
        similarity = float(data.pop("similarity"))
        return NearestMatch(record=MemoryRecord(**data), similarity=similarity)

    async def insert(
        self,
        content: str,
        category: MemoryCategory,
        embedding: list[float],
        decay_factor: float = 1.0,
    ) -> MemoryRecord:
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO memory_vectors (content, category, embedding, decay_factor)
            VALUES ($1, $2, $3::vector, $4)
            RETURNING {_RECORD_COLUMNS}
            """,
            content,
            category,
            to_pgvector(embedding),
            decay_factor,
        )
        assert row is not None
        return MemoryRecord(**dict(row))

    async def refresh(self, record_id: int) -> MemoryRecord:
        """Re-assert an existing fact: bump access_count, keep valid_at open.

        This is the dedup path. It deliberately does not touch created_at —
        decay should reflect when the preference was first learned, not the last
        time something similar was said, or a frequently repeated fact would
        never age.
        """
        row = await self._pool.fetchrow(
            f"""
            UPDATE memory_vectors
            SET access_count = access_count + 1,
                last_accessed_at = now(),
                valid_at = NULL
            WHERE id = $1
            RETURNING {_RECORD_COLUMNS}
            """,
            record_id,
        )
        if row is None:
            raise KeyError(f"memory {record_id} not found")
        return MemoryRecord(**dict(row))

    async def supersede(self, old_id: int, new_id: int) -> MemoryRecord:
        """Close an outdated record and point it at its replacement."""
        row = await self._pool.fetchrow(
            f"""
            UPDATE memory_vectors
            SET valid_at = now(), superseded_by = $2
            WHERE id = $1 AND valid_at IS NULL
            RETURNING {_RECORD_COLUMNS}
            """,
            old_id,
            new_id,
        )
        if row is None:
            raise KeyError(f"memory {old_id} not found or already closed")
        return MemoryRecord(**dict(row))

    def classify_write(self, similarity: float | None) -> UpsertAction:
        if similarity is not None and similarity >= self._dedup_threshold:
            return "refreshed"
        return "inserted"

    # --- reads ------------------------------------------------------------

    async def search(
        self,
        embedding: list[float],
        *,
        top_k: int = 5,
        time_window_days: int | None = None,
        categories: list[MemoryCategory] | None = None,
        include_superseded: bool = False,
    ) -> list[MemoryHit]:
        """Top-K by cosine similarity discounted by age.

        The ORDER BY repeats the score expression rather than referencing the
        alias so the planner can use it directly; Postgres does not allow an
        output alias in ORDER BY when it is wrapped in an expression.
        """
        rows = await self._pool.fetch(
            f"""
            WITH scored AS (
                SELECT {_RECORD_COLUMNS},
                       1 - (embedding <=> $1::vector) AS cosine_similarity,
                       EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS age_days
                FROM memory_vectors
                WHERE ($3::boolean OR valid_at IS NULL)
                  AND ($4::int IS NULL
                       OR created_at >= now() - make_interval(days => $4::int))
                  AND ($5::text[] IS NULL OR category = ANY($5::text[]))
            )
            SELECT *,
                   exp(-$6::double precision * decay_factor * age_days)
                     * CASE WHEN valid_at IS NULL THEN 1.0 ELSE $7::double precision END
                     AS decay_multiplier,
                   cosine_similarity
                     * exp(-$6::double precision * decay_factor * age_days)
                     * CASE WHEN valid_at IS NULL THEN 1.0 ELSE $7::double precision END
                     AS score
            FROM scored
            ORDER BY score DESC
            LIMIT $2
            """,
            to_pgvector(embedding),
            top_k,
            include_superseded,
            time_window_days,
            [str(category) for category in categories] if categories else None,
            self._decay_rate,
            self._superseded_penalty,
        )
        hits = [MemoryHit(**dict(row)) for row in rows]
        if hits:
            await self._record_access([hit.id for hit in hits])
        return hits

    async def _record_access(self, ids: list[int]) -> None:
        await self._pool.execute(
            """
            UPDATE memory_vectors
            SET access_count = access_count + 1, last_accessed_at = now()
            WHERE id = ANY($1::bigint[])
            """,
            ids,
        )

    async def count(self, include_superseded: bool = True) -> int:
        return int(
            await self._pool.fetchval(
                """
                SELECT count(*) FROM memory_vectors
                WHERE ($1::boolean OR valid_at IS NULL)
                """,
                include_superseded,
            )
            or 0
        )

    async def purge_all(self) -> None:
        """Test/eval helper: truncate T3. Never called by the API."""
        await self._pool.execute(
            "TRUNCATE memory_vectors RESTART IDENTITY CASCADE"
        )

    async def backdate(self, record_id: int, days: float) -> MemoryRecord:
        """Test/eval helper: move created_at into the past.

        Lets a benchmark verify exp(-λ·Δt) against a known age instead of
        waiting real days for a record to decay. Never called by the API.
        """
        row = await self._pool.fetchrow(
            f"""
            UPDATE memory_vectors
            SET created_at = now() - make_interval(secs => $2)
            WHERE id = $1
            RETURNING {_RECORD_COLUMNS}
            """,
            record_id,
            days * 86400.0,
        )
        if row is None:
            raise KeyError(f"memory {record_id} not found")
        return MemoryRecord(**dict(row))
