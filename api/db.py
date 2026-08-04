"""Postgres access. Raw SQL over asyncpg, so the decay formula stays readable."""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from .settings import Settings

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        dsn=str(settings.database_url),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    if pool is None:  # pragma: no cover - asyncpg only returns None on misuse
        raise RuntimeError("asyncpg returned no pool")
    return pool


async def apply_schema(pool: asyncpg.Pool, settings: Settings) -> None:
    """Idempotent DDL. Safe to run on every boot."""
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
