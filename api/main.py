"""FastAPI transport over MemoryService."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status

from .models import (
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
) -> list[SummaryCard]:
    return await service.list_summaries(session_id, limit)


# --- T3 -------------------------------------------------------------------


@app.post("/memories", tags=["T3 vector memory"])
async def upsert_preference(
    request: UpsertPreferenceRequest, service: ServiceDep
) -> UpsertPreferenceResult:
    """Same code path as the MCP `upsert_preference` tool."""
    return await service.upsert_preference(request)


@app.post("/memories/query", tags=["T3 vector memory"])
async def temporal_query(
    request: TemporalQueryRequest, service: ServiceDep
) -> TemporalQueryResult:
    """Same code path as the MCP `temporal_query` tool."""
    return await service.temporal_query(request)
