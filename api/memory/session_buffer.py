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
