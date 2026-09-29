"""Per-file resume points, so ingestion is incremental.

中文说明：保存"每个转录文件读到了哪里"的记录，这样每次摄取（ingest）都可以
从上次停下的地方继续，而不用每次都重新读整个文件。
"""

from __future__ import annotations

import asyncpg

from .models import FileCursor, SourceKind


class CursorStore:
    """Postgres-backed storage for FileCursor rows, keyed by (source, path).

    中文：基于 Postgres 存储 FileCursor 记录，以 (source, path) 作为主键。
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Store the connection pool used for all queries below.

        中文：保存后续所有查询要用的数据库连接池。

        Args:
            pool: An asyncpg connection pool. asyncpg 连接池。
        """
        self._pool = pool

    async def get(self, source: SourceKind, path: str) -> FileCursor:
        """Fetch the saved cursor for one file, or a fresh one if none exists.

        中文：读取某个文件已保存的游标；如果还没有记录过，返回一个全新的游标
        （相当于从头开始）。

        Args:
            source: Which reader this file belongs to. 该文件属于哪个读取器（来源）。
            path: Absolute path of the transcript file. 转录文件的绝对路径。

        Returns:
            The stored FileCursor, or a zeroed one for a never-seen path.
            已保存的 FileCursor；若该路径从未见过，则返回全零的初始游标。
        """
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
        """Persist how far ingestion got in one file.

        中文：保存这次摄取在某个文件里读到了多远。

        Note: `turns_ingested` is additive across runs (EXCLUDED value is added
        to the existing total), while `bytes_read` and `last_uuid` are simply
        overwritten with the latest value — they describe a position, not a
        running count.
        中文：注意 `turns_ingested` 在多次运行之间是累加的（新写入的值会加到
        已有总数上），而 `bytes_read` 和 `last_uuid` 只是被最新值覆盖——因为
        它们描述的是一个位置，而不是一个累计数量。

        Args:
            source: Which reader this file belongs to. 该文件属于哪个读取器（来源）。
            path: Absolute path of the transcript file. 转录文件的绝对路径。
            bytes_read: Byte offset ingestion reached this run. 本次摄取读到的字节偏移量。
            turns_ingested: New turns written this run. 本次新写入的对话轮数。
            last_uuid: Identity of the last turn ingested, if any.
        """
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
        """Delete saved cursors, forcing the next run to re-read from scratch.

        中文：删除已保存的游标，这样下一次运行就会从头重新读取。

        Args:
            source: Limit deletion to one source; None clears every source.
                只删除某一来源的游标；传 None 则清空所有来源。

        Returns:
            The number of cursor rows deleted. 被删除的游标行数。
        """
        result = await self._pool.execute(
            "DELETE FROM ingest_cursors WHERE ($1::text IS NULL OR source = $1)",
            source,
        )
        return int(result.split()[-1]) if result else 0

    async def summary(self) -> list[dict[str, object]]:
        """Aggregate cursor state per source, for `--status` reporting.

        中文：按来源汇总游标状态，供 `--status` 命令展示。

        Returns:
            One dict per source with file/byte/turn totals and last run time.
            每个来源一条记录，包含文件数、字节数、轮数总计和最近一次运行时间。
        """
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
