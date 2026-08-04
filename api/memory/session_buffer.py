"""T1 — session buffer: the last N raw turns of a conversation."""

from __future__ import annotations

import asyncpg

from ..models import SessionBuffer, Turn, TurnCreate
from .tokens import count_tokens


class SessionBufferStore:
    def __init__(self, pool: asyncpg.Pool, window: int) -> None:
        self._pool = pool
        self._window = window

    @property
    def window(self) -> int:
        return self._window

    async def append(self, session_id: str, turn: TurnCreate) -> Turn:
        row = await self._pool.fetchrow(
            """
            INSERT INTO session_turns (session_id, role, content, tool, token_count)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, session_id, role, content, tool, token_count, created_at
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
    async def append_many(
        self,
        rows: list[tuple[str, str, str, str | None, int, object, str]],
    ) -> int:
        """Bulk insert for Path A ingestion.

        Rows are (session_id, role, content, tool, token_count, created_at,
        source_key). ON CONFLICT on source_key makes re-ingesting a file a
        no-op, so a lost or reset cursor cannot produce duplicate turns.
        Returns how many rows were actually new.
        """
        if not rows:
            return 0
        inserted = await self._pool.fetchval(
            """
            WITH input AS (
                SELECT * FROM unnest(
                    $1::text[], $2::text[], $3::text[], $4::text[],
                    $5::int[], $6::timestamptz[], $7::text[]
                ) AS t(session_id, role, content, tool, token_count,
                       created_at, source_key)
            ), ins AS (
                INSERT INTO session_turns
                    (session_id, role, content, tool, token_count,
                     created_at, source_key)
                SELECT session_id, role, content, tool, token_count,
                       created_at, source_key
                FROM input
                ON CONFLICT (source_key) WHERE source_key IS NOT NULL
                DO NOTHING
                RETURNING 1
            )
            SELECT count(*) FROM ins
            """,
            [row[0] for row in rows],
            [row[1] for row in rows],
            [row[2] for row in rows],
            [row[3] for row in rows],
            [row[4] for row in rows],
            [row[5] for row in rows],
            [row[6] for row in rows],
        )
        return int(inserted or 0)

    async def read(self, session_id: str, window: int | None = None) -> SessionBuffer:
        """The live window, newest last, plus how many turns have aged out."""
        limit = window or self._window
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, role, content, tool, token_count, created_at
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

    async def evicted(self, session_id: str, window: int | None = None) -> list[Turn]:
        """Turns that have fallen out of the window — the input T2 compresses."""
        limit = window or self._window
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, role, content, tool, token_count, created_at
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
