"""FastAPI transport over MemoryService."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from pydantic import BaseModel

from .models import (
    CardScope,
    DailyReview,
    MemoryWithDecay,
    MemoryNamespace,
    MemoryMutationRequest,
    MemoryMutationResult,
    PatternCandidate,
    PatternCandidateCreate,
    PatternDecisionRequest,
    PatternStatus,
    SessionBuffer,
    SummaryCard,
    SummaryCardCreate,
    TemporalQueryRequest,
    TemporalQueryResult,
    Turn,
    TurnCreate,
    UpsertPreferenceRequest,
    UpsertPreferenceResult,
)
from .service import MemoryService
from .settings import get_settings

logging.basicConfig(level=logging.INFO)


class TurnWindow(BaseModel):
    """Turns in a range, plus how many exist beyond the returned page."""

    turns: list[Turn]
    total: int
    returned: int


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    service = await MemoryService.start(get_settings())
    app.state.service = service
    try:
        yield
    finally:
        await service.close()


app = FastAPI(
    title="MindBridge memory API",
    version="0.1.0",
    summary="Three-tier long-term memory for LLM clients.",
    lifespan=lifespan,
)


def get_service(request: Request) -> MemoryService:
    service: MemoryService | None = getattr(request.app.state, "service", None)
    if service is None:  # pragma: no cover - only if lifespan was skipped
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="memory service is not initialised",
        )
    return service


ServiceDep = Annotated[MemoryService, Depends(get_service)]
SessionId = Annotated[str, Path(min_length=1, max_length=128)]


@app.get("/healthz", tags=["ops"])
async def healthz(service: ServiceDep) -> dict[str, object]:
    return await service.health()


# --- T1 -------------------------------------------------------------------


@app.post(
    "/sessions/{session_id}/turns",
    tags=["T1 session buffer"],
    status_code=status.HTTP_201_CREATED,
)
async def append_turn(
    session_id: SessionId, turn: TurnCreate, service: ServiceDep
) -> Turn:
    return await service.add_turn(session_id, turn)


@app.get("/sessions/{session_id}/buffer", tags=["T1 session buffer"])
async def read_buffer(session_id: SessionId, service: ServiceDep) -> SessionBuffer:
    return await service.read_buffer(session_id)


# --- T2 -------------------------------------------------------------------


@app.post(
    "/summaries",
    tags=["T2 rolling summary"],
    status_code=status.HTTP_201_CREATED,
)
async def write_summary(card: SummaryCardCreate, service: ServiceDep) -> SummaryCard:
    return await service.write_summary(card)


@app.get("/summaries", tags=["T2 rolling summary"])
async def list_summaries(
    service: ServiceDep,
    session_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=365)] = 30,
    scope: Annotated[CardScope, Query()] = "day",
) -> list[SummaryCard]:
    """Day cards by default.

    Session cards live in the same table, so the scope is explicit: without it
    the diary's day list would silently fill with hundreds of session rows the
    moment per-session cards were written.
    """
    return await service.list_summaries(session_id, limit, scope)


# --- T3 -------------------------------------------------------------------


@app.post("/memories", tags=["T3 vector memory"])
async def upsert_preference(
    request: UpsertPreferenceRequest, service: ServiceDep
) -> UpsertPreferenceResult:
    """Same code path as the MCP `upsert_preference` tool."""
    return await service.upsert_preference(request)


@app.get("/memories", tags=["T3 vector memory"])
async def list_memories(
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_superseded: Annotated[bool, Query()] = True,
    namespace: Annotated[MemoryNamespace | None, Query()] = None,
) -> list[MemoryWithDecay]:
    """Newest-first listing for the diary timeline.

    Deliberately not a search: no query means no cosine term, so each row
    carries only its decay weight. This does not bump access_count — drawing a
    timeline is not the model recalling something.
    """
    namespaces = [namespace] if namespace is not None else None
    return await service.list_memories(limit, include_superseded, namespaces)


@app.get("/memories/{memory_id}", tags=["T3 vector memory"])
async def get_memory(
    memory_id: Annotated[int, Path(ge=1)],
    service: ServiceDep,
) -> MemoryWithDecay:
    try:
        return await service.get_memory(memory_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.patch("/memories/{memory_id}", tags=["T3 vector memory"])
async def mutate_memory(
    memory_id: Annotated[int, Path(ge=1)],
    request: MemoryMutationRequest,
    service: ServiceDep,
) -> MemoryMutationResult:
    try:
        if request.action == "archive":
            return await service.archive_memory(memory_id)
        return await service.edit_memory(memory_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@app.get("/turns", tags=["T1 session buffer"])
async def list_turns(
    service: ServiceDep,
    start: Annotated[datetime, Query(description="inclusive, ISO 8601")],
    end: Annotated[datetime, Query(description="exclusive, ISO 8601")],
    limit: Annotated[int, Query(ge=1, le=200)] = 40,
) -> TurnWindow:
    """Raw turns in a time range — what the 'look underneath' panel shows.

    The range is passed as timestamps rather than a date so the caller owns the
    timezone; the server never has to guess which midnight was meant.
    """
    turns, total = await service.list_turns_between(start, end, limit)
    return TurnWindow(turns=turns, total=total, returned=len(turns))


@app.post("/memories/query", tags=["T3 vector memory"])
async def temporal_query(
    request: TemporalQueryRequest, service: ServiceDep
) -> TemporalQueryResult:
    """Same code path as the MCP `temporal_query` tool."""
    return await service.temporal_query(request)


@app.post("/patterns", tags=["Reflective patterns"])
async def propose_pattern(
    request: PatternCandidateCreate, service: ServiceDep
) -> PatternCandidate:
    """Create a reviewable inference outside T3."""
    return await service.propose_pattern(request)


@app.get("/patterns", tags=["Reflective patterns"])
async def list_patterns(
    service: ServiceDep,
    pattern_status: Annotated[PatternStatus | None, Query(alias="status")] = "pending",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[PatternCandidate]:
    return await service.list_patterns(status=pattern_status, limit=limit)


@app.post("/patterns/{candidate_id}/resolve", tags=["Reflective patterns"])
async def resolve_pattern(
    candidate_id: Annotated[int, Path(ge=1)],
    request: PatternDecisionRequest,
    service: ServiceDep,
) -> PatternCandidate:
    try:
        return await service.resolve_pattern(candidate_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@app.get("/daily-review", tags=["Companion review"])
async def daily_review(
    service: ServiceDep,
    period: Annotated[str, Query()] = "latest",
) -> DailyReview:
    return await service.daily_review(period)
