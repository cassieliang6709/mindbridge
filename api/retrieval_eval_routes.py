"""Local developer routes for reviewing retrieval labels and rerunning evals."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from evals.retrieval_quality import (
    DEFAULT_CASES_PATH,
    DEFAULT_OUTPUT_PATH,
    RetrievalJudgement,
    evaluate,
    load_dataset,
    save_dataset,
    save_result,
)

from .service import MemoryService

router = APIRouter(prefix="/retrieval-evals", tags=["Retrieval evaluation"])
_write_lock = asyncio.Lock()


class ConfirmJudgementRequest(BaseModel):
    """Explicit human decision for one local retrieval case."""

    relevant_memory_ids: list[int] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=600)


def get_service(request: Request) -> MemoryService:
    service: MemoryService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - only if lifespan was skipped
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory service is not initialised",
        )
    return service


ServiceDep = Annotated[MemoryService, Depends(get_service)]


def _read_latest() -> dict[str, object] | None:
    if not DEFAULT_OUTPUT_PATH.exists():
        return None
    value = json.loads(DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


@router.get("")
async def read_retrieval_eval() -> dict[str, object]:
    dataset, receipt = load_dataset(DEFAULT_CASES_PATH)
    return {
        "dataset": dataset.model_dump(mode="json"),
        "dataset_sha256": receipt,
        "latest": _read_latest(),
    }


@router.post("/run")
async def run_retrieval_eval(service: ServiceDep) -> dict[str, object]:
    async with _write_lock:
        dataset, receipt = load_dataset(DEFAULT_CASES_PATH)
        result = await evaluate(dataset, receipt, service)
        save_result(DEFAULT_OUTPUT_PATH, result)
    return result


@router.post("/{case_id}/confirm")
async def confirm_retrieval_judgement(
    case_id: str,
    request: ConfirmJudgementRequest,
    service: ServiceDep,
) -> dict[str, object]:
    selected = sorted(set(request.relevant_memory_ids))
    latest = _read_latest()
    latest_case = next(
        (
            item
            for item in (latest or {}).get("cases", [])
            if item.get("case_id") == case_id
        ),
        None,
    )
    if latest_case is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="run the baseline before confirming candidate relevance",
        )
    candidate_ids = sorted(
        {
            int(hit["memory_id"])
            for hit in latest_case.get("hits", [])
            if "memory_id" in hit
        }
    )
    if not candidate_ids:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the latest baseline has no candidate pool for this case",
        )
    if not set(selected).issubset(candidate_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="relevant IDs must come from the displayed candidate pool",
        )

    for memory_id in selected:
        try:
            await service.get_memory(memory_id)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"memory {memory_id} does not exist",
            ) from error

    async with _write_lock:
        dataset, _ = load_dataset(DEFAULT_CASES_PATH)
        case = next((item for item in dataset.cases if item.case_id == case_id), None)
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"retrieval case {case_id!r} not found",
            )
        case.judgement = RetrievalJudgement(
            status="confirmed",
            judged_memory_ids=candidate_ids,
            relevant_memory_ids=selected,
            labelled_by="Cassie (local retrieval UI)",
            labelled_at=datetime.now(timezone.utc),
            notes=request.notes,
        )
        save_dataset(DEFAULT_CASES_PATH, dataset)
        dataset, receipt = load_dataset(DEFAULT_CASES_PATH)
        result = await evaluate(dataset, receipt, service)
        save_result(DEFAULT_OUTPUT_PATH, result)

    return {
        "dataset": dataset.model_dump(mode="json"),
        "dataset_sha256": receipt,
        "latest": result,
    }
