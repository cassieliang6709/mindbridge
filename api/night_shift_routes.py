"""Operator API for the local Night Shift pipeline and review inbox."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from pydantic import BaseModel, Field

from evals.retrieval_quality import DEFAULT_CASES_PATH, DEFAULT_OUTPUT_PATH, load_dataset

from .background_jobs import BackgroundJob, BackgroundJobCreate, BackgroundJobStore
from .job_dispatch import dispatch_jobs
from .memory_candidates import (
    MemoryCandidate,
    MemoryCandidateDecisionRequest,
    MemoryCandidateStatus,
)
from .models import CardScope
from .service import MemoryService

router = APIRouter(prefix="/night-shift", tags=["Night Shift"])


class NightShiftRunRequest(BaseModel):
    extract_missing: bool = True
    run_retrieval_eval: bool = True
    scope: CardScope = "day"
    limit: int = Field(default=3, ge=1, le=25)


def get_service(request: Request) -> MemoryService:
    service: MemoryService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory service is not initialised",
        )
    return service


ServiceDep = Annotated[MemoryService, Depends(get_service)]


def _latest_eval() -> dict[str, object] | None:
    if not DEFAULT_OUTPUT_PATH.exists():
        return None
    value = json.loads(DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


@router.get("")
async def dashboard(
    service: ServiceDep,
    job_limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, object]:
    store = BackgroundJobStore(service._pool)
    return {
        "jobs": [job.model_dump(mode="json") for job in await store.list(job_limit)],
        "memory_candidates": [
            item.model_dump(mode="json")
            for item in await service.list_memory_candidates(status="pending", limit=50)
        ],
        "pattern_candidates": [
            item.model_dump(mode="json")
            for item in await service.list_patterns(status="pending", limit=30)
        ],
        "latest_eval": _latest_eval(),
    }


@router.post("/run")
async def run_night_shift(
    request: NightShiftRunRequest,
    service: ServiceDep,
) -> dict[str, object]:
    store = BackgroundJobStore(service._pool)
    created: list[BackgroundJob] = []

    if request.extract_missing:
        cards = await service.list_summaries(limit=365, scope=request.scope)
        missing = [card for card in cards if not card.narrative][: request.limit]
        for card in missing:
            created.append(
                await store.create(
                    BackgroundJobCreate(
                        kind="extract_card",
                        payload={
                            "pipeline_version": "night-shift.v1",
                            "period": card.period,
                            "session_id": card.session_id,
                            "provider": "mlx",
                            "timezone": "America/New_York",
                            "min_confidence": 0.7,
                        },
                    )
                )
            )

    if request.run_retrieval_eval:
        _, dataset_receipt = load_dataset(DEFAULT_CASES_PATH)
        corpus_revision = await service._pool.fetchval(
            """
            SELECT concat(COALESCE(max(id), 0), ':', count(*), ':',
                          COALESCE(max(created_at), to_timestamp(0)), ':',
                          COALESCE(max(valid_at), to_timestamp(0)))
            FROM memory_vectors
            """
        )
        created.append(
            await store.create(
                BackgroundJobCreate(
                    kind="retrieval_eval",
                    payload={
                        "evaluator_version": "retrieval-quality.v1",
                        "dataset_sha256": dataset_receipt,
                        "corpus_revision": str(corpus_revision),
                    },
                )
            )
        )

    dispatchable = [job for job in created if job.status in {"queued", "retrying"}]
    sent, errors = await dispatch_jobs(store, dispatchable)
    return {
        "jobs": [job.model_dump(mode="json") for job in created],
        "dispatched": [job.job_id for job in sent],
        "dispatch_errors": errors,
    }


@router.post("/dispatch")
async def reconcile_and_dispatch(service: ServiceDep) -> dict[str, object]:
    store = BackgroundJobStore(service._pool)
    stale = await store.requeue_stale()
    queued = await store.dispatchable()
    sent, errors = await dispatch_jobs(store, queued)
    return {
        "requeued_stale": [job.job_id for job in stale],
        "dispatched": [job.job_id for job in sent],
        "dispatch_errors": errors,
    }


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: Annotated[str, Path(min_length=1, max_length=128)],
    service: ServiceDep,
) -> BackgroundJob:
    job = await BackgroundJobStore(service._pool).get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


@router.post("/jobs/{job_id}/retry")
async def retry_job(
    job_id: Annotated[str, Path(min_length=1, max_length=128)],
    service: ServiceDep,
) -> dict[str, object]:
    store = BackgroundJobStore(service._pool)
    try:
        job = await store.retry_failed(job_id)
    except KeyError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    sent, errors = await dispatch_jobs(store, [job])
    return {
        "job": job.model_dump(mode="json"),
        "dispatched": bool(sent),
        "dispatch_errors": errors,
    }


@router.get("/candidates")
async def list_candidates(
    service: ServiceDep,
    candidate_status: Annotated[
        MemoryCandidateStatus | None, Query(alias="status")
    ] = "pending",
) -> list[MemoryCandidate]:
    return await service.list_memory_candidates(status=candidate_status, limit=100)


@router.post("/candidates/{candidate_id}/resolve")
async def resolve_candidate(
    candidate_id: Annotated[int, Path(ge=1)],
    request: MemoryCandidateDecisionRequest,
    service: ServiceDep,
) -> MemoryCandidate:
    try:
        return await service.resolve_memory_candidate(candidate_id, request)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from error
