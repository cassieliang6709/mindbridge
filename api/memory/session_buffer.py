"""T1 — session buffer: the last N raw turns of a conversation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import asyncpg

from ..models import SessionBuffer, Turn, TurnCreate
from .tokens import count_tokens


@dataclass(slots=True)
class IngestRow:
    """One turn on its way into T1 from a transcript reader."""

    # 中文：从转录读取器写入 T1 前的一条记录，带有去重和来源元数据。

    session_id: str
    role: str
    content: str
    tool: str | None
    token_count: int
    created_at: datetime
    source_key: str
    project: str | None = None
    git_branch: str | None = None
    tool_names: list[str] = field(default_factory=list)


class SessionBufferStore:
    """Persist and retrieve raw T1 turns in PostgreSQL.

    中文：在 PostgreSQL 中持久化并读取原始 T1 记录。
    """

    def __init__(self, pool: asyncpg.Pool, window: int) -> None:
        """Create a T1 store with one pool and its default live-window size.

        中文：使用一个数据库连接池和默认实时窗口大小创建 T1 存储。

        Args:
            pool: Open PostgreSQL connection pool.
            window: Maximum number of newest turns returned by ``read`` by
                default.
        """
        self._pool = pool
        self._window = window

    @property
    def window(self) -> int:
        """Return the configured default number of live T1 turns.

        中文：返回配置的默认实时 T1 记录窗口大小。
        """
        return self._window

    async def append(self, session_id: str, turn: TurnCreate) -> Turn:
        """Store one API-created turn and calculate its token count.

        中文：存储一条由 API 创建的记录，并计算其 token 数量。
        """
        row = await self._pool.fetchrow(
            """
            INSERT INTO session_turns (session_id, role, content, tool, token_count)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, session_id, role, content, tool, token_count, created_at,
                      project
            """,
            session_id,
            turn.role,
            turn.content,
            turn.tool,
            count_tokens(turn.content),
        )
        assert row is not None
        return Turn(**dict(row))

    # NOTE for future readers: rows arriving here are one-turn-per-response.
    # Claude Code writes one JSONL record per content block and repeats the
    # response's usage on each, so the ingest reader merges them by message id
    # before this point. Summing per record inflated tokens by 2.5x.
    async def append_many(self, rows: list[IngestRow]) -> int:
        """Bulk insert for Path A ingestion.

        ON CONFLICT on source_key makes re-ingesting a file a no-op, so a lost
        or reset cursor cannot produce duplicate turns. Returns how many rows
        were actually new.
        """
        # 中文：按来源键批量写入，先折叠同一批次的重复键，重跑时只补充元数据。
        if not rows:
            return 0
        # Postgres rejects an ON CONFLICT DO UPDATE whose input proposes the
        # same key twice ("cannot affect row a second time"), and real
        # transcripts do repeat a uuid — the same session can be written under
        # two project directories. Collapse to the first occurrence, which is
        # also the one whose byte offset the cursor will record.
        seen: dict[str, IngestRow] = {}
        for row in rows:
            seen.setdefault(row.source_key, row)
        rows = list(seen.values())
        inserted = await self._pool.fetchval(
            """
            WITH input AS (
                SELECT * FROM unnest(
                    $1::text[], $2::text[], $3::text[], $4::text[],
                    $5::int[], $6::timestamptz[], $7::text[],
                    $8::text[], $9::text[], $10::jsonb[]
                ) AS t(session_id, role, content, tool, token_count,
                       created_at, source_key, project, git_branch, tool_names)
            ), ins AS (
                INSERT INTO session_turns
                    (session_id, role, content, tool, token_count,
                     created_at, source_key, project, git_branch, tool_names)
                SELECT session_id, role, content, tool, token_count,
                       created_at, source_key, project, git_branch, tool_names
                FROM input
                -- DO UPDATE rather than DO NOTHING so a re-run backfills
                -- metadata onto rows stored before those columns existed.
                -- COALESCE keeps existing values, so this can only add
                -- information, never blank a row out.
                ON CONFLICT (source_key) WHERE source_key IS NOT NULL
                DO UPDATE SET
                    project = COALESCE(EXCLUDED.project, session_turns.project),
                    git_branch = COALESCE(
                        EXCLUDED.git_branch, session_turns.git_branch
                    ),
                    tool_names = CASE
                        WHEN jsonb_array_length(session_turns.tool_names) = 0
                        THEN EXCLUDED.tool_names
                        ELSE session_turns.tool_names
                    END
                -- xmax = 0 identifies a genuine INSERT, so the caller can
                -- report new turns without counting backfilled ones.
                RETURNING (xmax = 0) AS inserted
            )
            SELECT count(*) FILTER (WHERE inserted) FROM ins
            """,
            [row.session_id for row in rows],
            [row.role for row in rows],
            [row.content for row in rows],
            [row.tool for row in rows],
            [row.token_count for row in rows],
            [row.created_at for row in rows],
            [row.source_key for row in rows],
            [row.project for row in rows],
            [row.git_branch for row in rows],
            [json.dumps(row.tool_names) for row in rows],
        )
        return int(inserted or 0)

    async def rows_for_digest(
        self, start: datetime, end: datetime
    ) -> list[asyncpg.Record]:
        """Every turn in a range, with the metadata a day card is built from.

        Unlike list_between() this has no limit: a card must describe the whole
        day,
        so a page of it would produce a card that understates the day.
        """
        # 中文：为构建日卡读取完整时间范围，不能分页，否则摘要会低估当天工作量。
        return list(
            await self._pool.fetch(
                """
                SELECT session_id, role, tool, token_count, created_at,
                       project, git_branch, tool_names
                FROM session_turns
                WHERE created_at >= $1 AND created_at < $2
                ORDER BY created_at ASC, id ASC
                """,
                start,
                end,
            )
        )

    async def read(self, session_id: str, window: int | None = None) -> SessionBuffer:
        """The live window, newest last, plus how many turns have aged out."""
        # 中文：读取会话实时窗口，按时间正序返回，并计算已被挤出的记录数。
        limit = window or self._window
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, role, content, tool, token_count, created_at,
                   project
            FROM (
                SELECT *
                FROM session_turns
                WHERE session_id = $1
                ORDER BY created_at DESC, id DESC
                LIMIT $2
            ) AS recent
            ORDER BY created_at ASC, id ASC
            """,
            session_id,
            limit,
        )
        total = await self._pool.fetchval(
            "SELECT count(*) FROM session_turns WHERE session_id = $1", session_id
        )
        turns = [Turn(**dict(row)) for row in rows]
        return SessionBuffer(
            session_id=session_id,
            window=limit,
            turns=turns,
            tokens_in_window=sum(turn.token_count for turn in turns),
            evicted_count=max(0, int(total or 0) - len(turns)),
        )

    async def list_between(
        self,
        start: datetime,
        end: datetime,
        limit: int = 50,
        source: str | None = None,
    ) -> list[Turn]:
        """Turns in a half-open [start, end) window, oldest first."""
        # 中文：读取半开时间区间内的记录，按最早到最新排序。
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, role, content, tool, token_count, created_at,
                   project
            FROM session_turns
            WHERE created_at >= $1 AND created_at < $2
              AND ($4::text IS NULL OR tool = $4)
            ORDER BY created_at ASC, id ASC
            LIMIT $3
            """,
            start,
            end,
            limit,
            source,
        )
        return [Turn(**dict(row)) for row in rows]

    async def count_between(self, start: datetime, end: datetime) -> int:
        """Count all T1 turns in a half-open time range.

        中文：统计半开时间范围内的全部 T1 记录数量。
        """
        return int(
            await self._pool.fetchval(
                """
                SELECT count(*) FROM session_turns
                WHERE created_at >= $1 AND created_at < $2
                """,
                start,
                end,
            )
            or 0
        )

    async def evicted(self, session_id: str, window: int | None = None) -> list[Turn]:
        """Turns that have fallen out of the window — the input T2 compresses."""
        # 中文：读取已从实时窗口移出的记录，它们是 T2 压缩总结的输入。
        limit = window or self._window
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, role, content, tool, token_count, created_at,
                   project
            FROM session_turns
            WHERE session_id = $1
            ORDER BY created_at ASC, id ASC
            OFFSET 0
            LIMIT GREATEST(
                (SELECT count(*) FROM session_turns WHERE session_id = $1) - $2, 0
            )
            """,
            session_id,
            limit,
        )
        return [Turn(**dict(row)) for row in rows]
