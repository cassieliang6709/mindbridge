"""Idempotent consumers for MindBridge background jobs."""

from __future__ import annotations

import asyncio
import httpx
from celery import Task

from api.background_jobs import BackgroundJobStore
from api.service import MemoryService
from api.settings import get_settings
from evals.retrieval_quality import (
    DEFAULT_CASES_PATH,
    DEFAULT_OUTPUT_PATH,
    evaluate,
    load_dataset,
    save_result,
)
from extract.background import ExtractionRejected, extract_card

from .celery_app import celery_app


class RetryableJobError(RuntimeError):
    pass


async def _execute_job(job_id: str) -> dict[str, object]:
    service = await MemoryService.start(get_settings())
    store = BackgroundJobStore(service._pool)
    try:
        job = await store.claim(job_id)
        if job is None:
            existing = await store.get(job_id)
            return {
                "job_id": job_id,
                "status": existing.status if existing else "missing",
                "noop": True,
            }

        try:
            if job.kind == "extract_card":
                result = await extract_card(service, job.payload)
            elif job.kind == "retrieval_eval":
                dataset, receipt = load_dataset(DEFAULT_CASES_PATH)
                evaluated = await evaluate(dataset, receipt, service)
                save_result(DEFAULT_OUTPUT_PATH, evaluated)
                result = {
                    "dataset_sha256": receipt,
                    "generated_at": evaluated["generated_at"],
                    "metrics": evaluated["metrics"],
                }
            else:  # pragma: no cover - database CHECK prevents this
                raise ValueError(f"unsupported job kind {job.kind}")
        except ExtractionRejected as error:
            failed = await store.fail(
                job_id,
                error_code="invalid_extraction",
                error_message=str(error),
                retry=False,
            )
            return {"job_id": job_id, "status": failed.status}
        except (httpx.TimeoutException, httpx.NetworkError, ConnectionError, TimeoutError) as error:
            failed = await store.fail(
                job_id,
                error_code="transient_dependency",
                error_message=str(error),
                retry=True,
            )
            if failed.status == "retrying":
                raise RetryableJobError(str(error)) from error
            return {"job_id": job_id, "status": failed.status}
        except Exception as error:
            await store.fail(
                job_id,
                error_code=type(error).__name__,
                error_message=str(error),
                retry=False,
            )
            raise

        completed = await store.succeed(job_id, result)
        return {"job_id": job_id, "status": completed.status, "result": result}
    finally:
        await service.close()


@celery_app.task(
    bind=True,
    name="mindbridge.run_background_job",
    autoretry_for=(),
    max_retries=2,
)
def run_background_job(self: Task, job_id: str) -> dict[str, object]:
    """Execute one durable receipt; duplicate deliveries become no-ops."""
    try:
        return asyncio.run(_execute_job(job_id))
    except RetryableJobError as error:
        countdown = min(60, 5 * (2 ** int(self.request.retries)))
        raise self.retry(exc=error, countdown=countdown)
