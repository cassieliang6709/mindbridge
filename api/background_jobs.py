"""Durable receipts for Celery-backed maintenance work.

Redis transports a job id; PostgreSQL owns the payload, lifecycle and result.
The split makes broker delivery explicitly at-least-once while keeping derived
writes idempotent and inspectable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from uuid import uuid4

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

BackgroundJobKind = Literal["extract_card", "retrieval_eval"]
BackgroundJobStatus = Literal[
    "queued", "running", "retrying", "succeeded", "failed"
]

_COLUMNS = """
    job_id, kind, idempotency_key, status, payload, result, attempt,
    max_attempts, celery_task_id, error_code, error_message, created_at,
    updated_at, started_at, heartbeat_at, finished_at
"""


def _json_object(value: object | None) -> dict[str, object] | None:
    if value is None:
        return None
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, Mapping):
        raise ValueError("background job JSONB value must be an object")
    return dict(decoded)


def canonical_job_key(kind: BackgroundJobKind, payload: Mapping[str, object]) -> str:
    """Stable identity for one logical request, independent of dict ordering."""
    canonical = json.dumps(
        {"kind": kind, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BackgroundJobCreate(BaseModel):
    kind: BackgroundJobKind
    payload: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)


class BackgroundJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: str
    kind: BackgroundJobKind
    idempotency_key: str
    status: BackgroundJobStatus
    payload: dict[str, object]
    result: dict[str, object] | None = None
    attempt: int
    max_attempts: int
    celery_task_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    finished_at: datetime | None = None


def _as_job(row: asyncpg.Record | Mapping[str, object]) -> BackgroundJob:
    payload = dict(row)
    payload["payload"] = _json_object(payload.get("payload")) or {}
    payload["result"] = _json_object(payload.get("result"))
    return BackgroundJob.model_validate(payload)


class BackgroundJobStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, draft: BackgroundJobCreate) -> BackgroundJob:
        key = draft.idempotency_key or canonical_job_key(draft.kind, draft.payload)
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO background_jobs
                (job_id, kind, idempotency_key, payload, max_attempts)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            ON CONFLICT (idempotency_key) DO UPDATE
            SET idempotency_key = EXCLUDED.idempotency_key
            RETURNING {_COLUMNS}
            """,
            f"job_{uuid4().hex}",
            draft.kind,
            key,
            json.dumps(draft.payload, ensure_ascii=False),
            draft.max_attempts,
        )
        assert row is not None
        return _as_job(row)

    async def get(self, job_id: str) -> BackgroundJob | None:
        row = await self._pool.fetchrow(
            f"SELECT {_COLUMNS} FROM background_jobs WHERE job_id = $1", job_id
        )
        return _as_job(row) if row is not None else None

    async def list(self, limit: int = 30) -> list[BackgroundJob]:
        rows = await self._pool.fetch(
            f"""
            SELECT {_COLUMNS} FROM background_jobs
            ORDER BY created_at DESC LIMIT $1
            """,
            limit,
        )
        return [_as_job(row) for row in rows]

    async def claim(self, job_id: str) -> BackgroundJob | None:
        """Claim queued/retrying work; terminal and already-running rows are no-ops."""
        row = await self._pool.fetchrow(
            f"""
            UPDATE background_jobs
            SET status = 'running',
                attempt = attempt + 1,
                started_at = COALESCE(started_at, now()),
                heartbeat_at = now(),
                updated_at = now(),
                error_code = NULL,
                error_message = NULL
            WHERE job_id = $1
              AND status IN ('queued', 'retrying')
              AND attempt < max_attempts
            RETURNING {_COLUMNS}
            """,
            job_id,
        )
        return _as_job(row) if row is not None else None

    async def attach_delivery(self, job_id: str, celery_task_id: str) -> None:
        await self._pool.execute(
            """
            UPDATE background_jobs
            SET celery_task_id = $2, updated_at = now()
            WHERE job_id = $1 AND status IN ('queued', 'retrying')
            """,
            job_id,
            celery_task_id,
        )

    async def succeed(self, job_id: str, result: Mapping[str, object]) -> BackgroundJob:
        row = await self._pool.fetchrow(
            f"""
            UPDATE background_jobs
            SET status = 'succeeded', result = $2::jsonb, updated_at = now(),
                heartbeat_at = now(), finished_at = now(), error_code = NULL,
                error_message = NULL
            WHERE job_id = $1 AND status = 'running'
            RETURNING {_COLUMNS}
            """,
            job_id,
            json.dumps(dict(result), ensure_ascii=False),
        )
        if row is None:
            existing = await self.get(job_id)
            if existing is None:
                raise KeyError(job_id)
            return existing
        return _as_job(row)

    async def fail(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        retry: bool,
    ) -> BackgroundJob:
        row = await self._pool.fetchrow(
            f"""
            UPDATE background_jobs
            SET status = CASE
                    WHEN $4 AND attempt < max_attempts THEN 'retrying'
                    ELSE 'failed'
                END,
                error_code = $2,
                error_message = left($3, 1200),
                updated_at = now(),
                heartbeat_at = now(),
                finished_at = CASE
                    WHEN $4 AND attempt < max_attempts THEN NULL ELSE now()
                END
            WHERE job_id = $1 AND status = 'running'
            RETURNING {_COLUMNS}
            """,
            job_id,
            error_code,
            error_message,
            retry,
        )
        if row is None:
            existing = await self.get(job_id)
            if existing is None:
                raise KeyError(job_id)
            return existing
        return _as_job(row)

    async def requeue_stale(self, stale_after_seconds: int = 900) -> list[BackgroundJob]:
        rows = await self._pool.fetch(
            f"""
            UPDATE background_jobs
            SET status = 'retrying', updated_at = now(),
                error_code = 'worker_lost',
                error_message = 'worker heartbeat expired; queued for redelivery'
            WHERE status = 'running'
              AND heartbeat_at < now() - ($1 * interval '1 second')
              AND attempt < max_attempts
            RETURNING {_COLUMNS}
            """,
            stale_after_seconds,
        )
        return [_as_job(row) for row in rows]

    async def dispatchable(self, limit: int = 50) -> list[BackgroundJob]:
        rows = await self._pool.fetch(
            f"""
            SELECT {_COLUMNS} FROM background_jobs
            WHERE status IN ('queued', 'retrying') AND attempt < max_attempts
            ORDER BY created_at ASC LIMIT $1
            """,
            limit,
        )
        return [_as_job(row) for row in rows]

    async def retry_failed(self, job_id: str) -> BackgroundJob:
        """Explicit operator retry; resets the bounded automatic-attempt budget."""
        row = await self._pool.fetchrow(
            f"""
            UPDATE background_jobs
            SET status = 'queued', attempt = 0, result = NULL,
                celery_task_id = NULL, error_code = NULL, error_message = NULL,
                updated_at = now(), started_at = NULL, heartbeat_at = NULL,
                finished_at = NULL
            WHERE job_id = $1 AND status = 'failed'
            RETURNING {_COLUMNS}
            """,
            job_id,
        )
        if row is None:
            existing = await self.get(job_id)
            if existing is None:
                raise KeyError(job_id)
            raise ValueError(f"job {job_id} is {existing.status}, not failed")
        return _as_job(row)
