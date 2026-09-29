"""Postgres access. Raw SQL over asyncpg, so the decay formula stays readable.

中文说明：通过 asyncpg 直接执行原生 SQL 访问 Postgres,不使用 ORM,
这样衰减公式(decay formula)等计算逻辑可以直接读懂 SQL 而不必翻译 ORM 语法。
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from .settings import Settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Open an asyncpg connection pool sized from settings.

    中文：根据配置创建 asyncpg 连接池。

    Args:
        settings: Provides the database DSN and min/max pool size.

    Returns:
        A ready-to-use asyncpg connection pool. 一个可直接使用的 asyncpg 连接池。

    Raises:
        RuntimeError: If asyncpg unexpectedly returns no pool. 若 asyncpg 异常地未返回连接池,则抛出。
    """
    pool = await asyncpg.create_pool(
        dsn=str(settings.database_url),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    if pool is None:  # pragma: no cover - asyncpg only returns None on misuse
        raise RuntimeError("asyncpg returned no pool")
    return pool


async def apply_schema(pool: asyncpg.Pool, settings: Settings) -> None:
    """Idempotent DDL. Safe to run on every boot.

    中文：幂等的建表/建索引 DDL,每次启动时执行都是安全的。

    Args:
        pool: The connection pool to run the DDL through. 用于执行 DDL 的连接池。
        settings: Supplies the embedding dimension substituted into the schema.
    """
    ddl = SCHEMA_PATH.read_text(encoding="utf-8").replace(
        "{embedding_dim}", str(settings.embedding_dim)
    )
    async with pool.acquire() as connection:
        await connection.execute(ddl)
    await _assert_vector_width(pool, settings)
    logger.info("schema applied (embedding_dim=%s)", settings.embedding_dim)


async def _assert_vector_width(pool: asyncpg.Pool, settings: Settings) -> None:
    """Fail loudly when the configured dim no longer matches the live column.

    CREATE TABLE IF NOT EXISTS silently keeps the old width, which would
    otherwise surface much later as an opaque insert error.

    中文：当配置的向量维度与数据库中实际列的宽度不一致时,立即明确报错。
    因为 CREATE TABLE IF NOT EXISTS 不会修改已存在表的列宽,若不在这里检查,
    问题会推迟到很久之后的一次插入失败时才以一个含义不明的错误出现。

    Args:
        pool: The connection pool used to inspect the live column. 用于查询实际列宽的连接池。
        settings: Supplies the expected embedding dimension. 提供期望的向量维度。

    Raises:
        RuntimeError: If the live column width disagrees with ``settings.embedding_dim``.
    """
    async with pool.acquire() as connection:
        width = await connection.fetchval(
            """
            SELECT atttypmod
            FROM pg_attribute
            WHERE attrelid = 'memory_vectors'::regclass
              AND attname = 'embedding'
            """
        )
    if width is not None and width > 0 and width != settings.embedding_dim:
        raise RuntimeError(
            f"memory_vectors.embedding is vector({width}) but "
            f"MINDBRIDGE_EMBEDDING_DIM is {settings.embedding_dim}. "
            "Recreate the table or set the dim back."
        )
