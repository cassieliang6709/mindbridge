"""T3 — long-term vector memory with time decay and write-time dedup.

Default (temporal) retrieval score:

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
    MemoryNamespace,
    MemoryRecord,
    MemoryWithDecay,
    RankingMode,
    UpsertAction,
)

# A preference scoped to a different project is usually irrelevant here, but
# not always — the same habit often shows up under two project names. 0.5 is
# enough to keep it out of the top hits without hiding it.
# 中文：其他项目的偏好通常不相关，但可能跨项目复用；0.5 会降低排序而不会隐藏记录。
_OTHER_PROJECT_PENALTY = 0.5

_RECORD_COLUMNS = """
    id, content, namespace, category, created_at, valid_at, superseded_by,
    access_count, decay_factor, project
"""


@dataclass(slots=True)
class NearestMatch:
    """A T3 record and its cosine similarity to an incoming embedding.

    中文：一条 T3 记录及其与待写入向量的余弦相似度。
    """

    record: MemoryRecord
    similarity: float


class VectorMemoryStore:
    """Persist, rank, and retain auditable T3 vector-memory records.

    中文：持久化、排序并保留可审计的 T3 向量记忆记录。
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        decay_rate_per_day: float,
        dedup_threshold: float,
        superseded_penalty: float,
    ) -> None:
        """Create a vector store with its measured ranking parameters.

        中文：使用经过测量的排序参数创建向量存储。

        Args:
            pool: Open PostgreSQL connection pool with pgvector enabled.
            decay_rate_per_day: Global daily exponential-decay rate.
            dedup_threshold: Similarity at which a write refreshes a record.
            superseded_penalty: Multiplier applied to closed records in reads.
        """
        self._pool = pool
        self._decay_rate = decay_rate_per_day
        self._dedup_threshold = dedup_threshold
        self._superseded_penalty = superseded_penalty

    @property
    def decay_rate_per_day(self) -> float:
        """Return the global daily decay rate used by this store.

        中文：返回该存储使用的全局每日衰减率。
        """
        return self._decay_rate

    @property
    def dedup_threshold(self) -> float:
        """Return the similarity threshold used for deduplicating writes.

        中文：返回写入去重使用的相似度阈值。
        """
        return self._dedup_threshold

    # --- writes -----------------------------------------------------------

    async def nearest_open(
        self,
        embedding: list[float],
        namespace: MemoryNamespace,
        category: MemoryCategory | None = None,
    ) -> NearestMatch | None:
        """Closest still-valid record, for the dedup decision."""
        # 中文：寻找最接近且仍有效的记录，为写入去重决策提供依据。
        row = await self._pool.fetchrow(
            f"""
            SELECT {_RECORD_COLUMNS},
                   1 - (embedding <=> $1::vector) AS similarity
            FROM memory_vectors
            WHERE valid_at IS NULL
              AND namespace = $2
              AND ($3::text IS NULL OR category = $3)
            ORDER BY embedding <=> $1::vector
            LIMIT 1
            """,
            to_pgvector(embedding),
            namespace,
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
        namespace: MemoryNamespace,
        category: MemoryCategory,
        embedding: list[float],
        decay_factor: float = 1.0,
        project: str | None = None,
    ) -> MemoryRecord:
        """Insert a new open T3 record with its embedding.

        中文：插入一条仍有效的新 T3 记录及其向量嵌入。
        """
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO memory_vectors
                (content, namespace, category, embedding, decay_factor, project)
            VALUES ($1, $2, $3, $4::vector, $5, $6)
            RETURNING {_RECORD_COLUMNS}
            """,
            content,
            namespace,
            category,
            to_pgvector(embedding),
            decay_factor,
            project,
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
        # 中文：去重命中时只提升访问记录，不改变首次学习时间，避免重复事实
        # 永不衰减。
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
        # 中文：关闭过时记录，并链接到它的替代记录。
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

    async def archive(self, memory_id: int) -> MemoryRecord:
        """Close one open memory without replacement.

        Archive is an intentional, non-destructive state change. `superseded_by`
        is left as-is so the timeline stays stable for audit tools.
        """
        # 中文：归档是可审计的状态变化，不删除历史也不改写替代关系。
        row = await self._pool.fetchrow(
            f"""
            UPDATE memory_vectors
            SET valid_at = now()
            WHERE id = $1 AND valid_at IS NULL
            RETURNING {_RECORD_COLUMNS}
            """,
            memory_id,
        )
        if row is None:
            raise KeyError(f"memory {memory_id} not found or already closed")
        return MemoryRecord(**dict(row))

    async def edit(
        self,
        memory_id: int,
        content: str,
        embedding: list[float],
        decay_factor: float | None = None,
    ) -> MemoryRecord:
        """Replace one open memory with a user-confirmed edited version.

        Keeps the same namespace/category/project and decay_factor by default;
        the caller may provide a custom decay_factor for the replacement.
        """
        # 中文：先插入替代记录再关闭旧记录；事务中断时仍可保留可审计的历史链。
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    f"""
                    SELECT {_RECORD_COLUMNS}
                    FROM memory_vectors
                    WHERE id = $1 FOR UPDATE
                    """,
                    memory_id,
                )
                if row is None:
                    raise KeyError(f"memory {memory_id} not found")

                old_record = MemoryRecord(**dict(row))
                if old_record.valid_at is not None:
                    raise ValueError(f"memory {memory_id} is already closed")

                # Insert replacement first, then close old so history remains
                # auditable even on an interrupted transaction.
                new_row = await conn.fetchrow(
                    f"""
                    INSERT INTO memory_vectors
                        (content, namespace, category, embedding, decay_factor, project)
                    VALUES ($1, $2, $3, $4::vector, $5, $6)
                    RETURNING {_RECORD_COLUMNS}
                    """,
                    content,
                    old_record.namespace,
                    old_record.category,
                    to_pgvector(embedding),
                    decay_factor if decay_factor is not None else old_record.decay_factor,
                    old_record.project,
                )
                new_record = MemoryRecord(**dict(new_row))

                await conn.execute(
                    f"""
                    UPDATE memory_vectors
                    SET valid_at = now(), superseded_by = $2
                    WHERE id = $1
                    """,
                    memory_id,
                    new_record.id,
                )
        return new_record

    async def get(self, memory_id: int) -> MemoryRecord:
        """Read one T3 record without calculating a decay weight.

        中文：读取一条 T3 记录，但不计算时间衰减权重。
        """
        row = await self._pool.fetchrow(
            f"""
            SELECT {_RECORD_COLUMNS}
            FROM memory_vectors
            WHERE id = $1
            """,
            memory_id,
        )
        if row is None:
            raise KeyError(f"memory {memory_id} not found")
        return MemoryRecord(**dict(row))

    async def get_with_decay(self, memory_id: int) -> MemoryWithDecay:
        """Read one T3 record together with its current decay weight.

        中文：读取一条 T3 记录及其当前时间衰减权重。
        """
        row = await self._pool.fetchrow(
            f"""
            SELECT {_RECORD_COLUMNS},
                   EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS age_days,
                   exp(-$2::double precision * decay_factor
                     * EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0)
                     * CASE WHEN valid_at IS NULL THEN 1.0 ELSE $3::double precision END
                     AS decay_multiplier
            FROM memory_vectors
            WHERE id = $1
            """,
            memory_id,
            self._decay_rate,
            self._superseded_penalty,
        )
        if row is None:
            raise KeyError(f"memory {memory_id} not found")
        return MemoryWithDecay(**dict(row))

    def classify_write(self, similarity: float | None) -> UpsertAction:
        """Classify a similarity as a refresh or a new insert.

        中文：依据相似度将写入判定为刷新已有记录或插入新记录。
        """
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
        namespaces: list[MemoryNamespace] | None = None,
        include_superseded: bool = False,
        project: str | None = None,
        ranking_mode: RankingMode = "temporal",
        record_access: bool = True,
    ) -> list[MemoryHit]:
        """Top-K by temporal or semantic relevance.

        ``temporal`` preserves the legacy cosine-times-decay ranking. ``semantic``
        uses cosine similarity (and the existing project mismatch penalty) for
        ordering, while still returning age and decay values for audit.

        `record_access` exists for measurement, not for the product path. Every
        ranked read normally bumps access_count, which feeds ranking; a
        benchmark or eval that sweeps the same queries would therefore promote
        exactly the rows it is trying to score. Callers that are observing the
        system rather than using it pass False.

        `project` scopes the result without filtering it. A preference tied to
        another project is pushed down rather than removed, because the scope
        was assigned by a model and a wrong scope should cost rank, not erase
        the memory. Global rows (project IS NULL) are never penalised — they
        hold everywhere by definition.

        The ORDER BY repeats the score expression rather than referencing the
        alias so the planner can use it directly; Postgres does not allow an
        output alias in ORDER BY when it is wrapped in an expression.
        """
        # 中文：项目范围只降低其他项目记录的排序，不过滤掉它们；便于纠正
        # 模型误判范围。
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
                  AND ($6::text[] IS NULL OR namespace = ANY($6::text[]))
            )
            SELECT *,
                   exp(-$7::double precision * decay_factor * age_days)
                     * CASE WHEN valid_at IS NULL THEN 1.0 ELSE $8::double precision END
                     AS decay_multiplier,
                   CASE WHEN $11::text = 'semantic'
                        THEN cosine_similarity
                          * CASE
                              WHEN $9::text IS NULL THEN 1.0
                              WHEN project IS NULL THEN 1.0
                              WHEN project = $9::text THEN 1.0
                              ELSE $10::double precision
                            END
                        ELSE cosine_similarity
                          * exp(-$7::double precision * decay_factor * age_days)
                          * CASE WHEN valid_at IS NULL THEN 1.0 ELSE $8::double precision END
                          * CASE
                              WHEN $9::text IS NULL THEN 1.0
                              WHEN project IS NULL THEN 1.0
                              WHEN project = $9::text THEN 1.0
                              ELSE $10::double precision
                            END
                   END AS score
            FROM scored
            ORDER BY score DESC
            LIMIT $2
            """,
            to_pgvector(embedding),
            top_k,
            include_superseded,
            time_window_days,
            [str(category) for category in categories] if categories else None,
            [str(namespace) for namespace in namespaces] if namespaces else None,
            self._decay_rate,
            self._superseded_penalty,
            project,
            _OTHER_PROJECT_PENALTY,
            ranking_mode,
        )
        hits = [MemoryHit(**dict(row)) for row in rows]
        if hits and record_access:
            await self._record_access([hit.id for hit in hits])
        return hits

    async def list_recent(
        self,
        *,
        limit: int = 50,
        include_superseded: bool = True,
        namespaces: list[MemoryNamespace] | None = None,
    ) -> list[MemoryWithDecay]:
        """Newest-first listing for the diary timeline.

        Unlike search() this does not bump access_count: rendering a timeline is
        not the model recalling a memory, and conflating the two would inflate
        the access statistics.
        """
        # 中文：时间线展示不计为模型回忆，避免仅浏览就虚增访问统计。
        rows = await self._pool.fetch(
            f"""
            WITH aged AS (
                SELECT {_RECORD_COLUMNS},
                       EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS age_days
                FROM memory_vectors
                WHERE ($2::boolean OR valid_at IS NULL)
                  AND ($3::text[] IS NULL OR namespace = ANY($3::text[]))
            )
            SELECT *,
                   exp(-$4::double precision * decay_factor * age_days)
                     * CASE WHEN valid_at IS NULL
                            THEN 1.0 ELSE $5::double precision END
                     AS decay_multiplier
            FROM aged
            ORDER BY created_at DESC, id DESC
            LIMIT $1
            """,
            limit,
            include_superseded,
            [str(namespace) for namespace in namespaces] if namespaces else None,
            self._decay_rate,
            self._superseded_penalty,
        )
        return [MemoryWithDecay(**dict(row)) for row in rows]

    async def _record_access(self, ids: list[int]) -> None:
        """Record that ranked retrieval returned these memory ids.

        中文：记录这些记忆 ID 被排序检索返回过。
        """
        await self._pool.execute(
            """
            UPDATE memory_vectors
            SET access_count = access_count + 1, last_accessed_at = now()
            WHERE id = ANY($1::bigint[])
            """,
            ids,
        )

    async def count(self, include_superseded: bool = True) -> int:
        """Count T3 records, optionally including closed history.

        中文：统计 T3 记录数量，并可选择是否包含已关闭的历史记录。
        """
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
        # 中文：仅用于测试与评估；API 永不调用，因此生产路径不会清空 T3。
        await self._pool.execute(
            "TRUNCATE memory_vectors RESTART IDENTITY CASCADE"
        )

    async def backdate(self, record_id: int, days: float) -> MemoryRecord:
        """Test/eval helper: move created_at into the past.

        Lets a benchmark verify exp(-λ·Δt) against a known age instead of
        waiting real days for a record to decay. Never called by the API.
        """
        # 中文：仅用于测试与评估，方便以确定年龄验证指数衰减，而无需等待
        # 真实时间流逝。
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
