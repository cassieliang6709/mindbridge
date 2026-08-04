"""Per-file resume points, so ingestion is incremental."""

from __future__ import annotations

import asyncpg

from .models import FileCursor, SourceKind


class CursorStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, source: SourceKind, path: str) -> FileCursor:
        row = await self._pool.fetchrow(
            """
            SELECT source, path, bytes_read, turns_ingested, last_uuid, updated_at
            FROM ingest_cursors
            WHERE source = $1 AND path = $2
            """,
            source,
            path,
        )
        if row is None:
            return FileCursor(source=source, path=path)
        return FileCursor(**dict(row))

    async def save(
        self,
        source: SourceKind,
        path: str,
        bytes_read: int,
        turns_ingested: int,
        last_uuid: str | None,
    ) -> None:
        await self._pool.execute(
            """
            INSERT INTO ingest_cursors
                (source, path, bytes_read, turns_ingested, last_uuid, updated_at)
            VALUES ($1, $2, $3, $4, $5, now())
            ON CONFLICT (source, path) DO UPDATE SET
                bytes_read = EXCLUDED.bytes_read,
                turns_ingested = ingest_cursors.turns_ingested
                                 + EXCLUDED.turns_ingested,
                last_uuid = EXCLUDED.last_uuid,
                updated_at = now()
            """,
            source,
            path,
            bytes_read,
            turns_ingested,
            last_uuid,
        )

    async def reset(self, source: SourceKind | None = None) -> int:
        result = await self._pool.execute(
            "DELETE FROM ingest_cursors WHERE ($1::text IS NULL OR source = $1)",
            source,
        )
        return int(result.split()[-1]) if result else 0

    async def summary(self) -> list[dict[str, object]]:
        rows = await self._pool.fetch(
            """
            SELECT source,
                   count(*) AS files,
                   sum(bytes_read) AS bytes_read,
                   sum(turns_ingested) AS turns,
                   max(updated_at) AS last_run
            FROM ingest_cursors
            GROUP BY source
            ORDER BY source
            """
        )
        return [dict(row) for row in rows]
