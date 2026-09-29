"""HTTP surface for the local agent-runtime demonstration.

The routes expose the same runtime object the harness uses. They do not add a
second execution path in the UI, so a browser demo and a test exercise the same
ledger, retry rules, and memory service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .agent_runtime import (
    MemoryAgentRuntime,
    PreviousToolFailure,
    UncertainToolOutcome,
)
from .models import TemporalQueryRequest, TemporalQueryResult
from .local_agent import AgentAnswer, LocalMemoryAgent
from .run_ledger import (
    ReplaySnapshot,
    RunConfigConflict,
    RunEventConflict,
    RunNotResumable,
)

router = APIRouter(prefix="/agent-runs", tags=["Agent runtime"])


class StartRunRequest(BaseModel):
    """User task and optional stable id for an idempotent run start."""

    task: str = Field(min_length=1, max_length=500)
    run_id: str | None = Field(default=None, min_length=1, max_length=160)


class QueryToolRequest(BaseModel):
    """One explicit read-tool call made inside an existing run."""

    tool_call_id: str = Field(min_length=1, max_length=160)
    query: TemporalQueryRequest


class QueryToolResponse(BaseModel):
    """Tool result together with the trace persisted after execution."""

    result: TemporalQueryResult
    snapshot: ReplaySnapshot


class CompleteRunRequest(BaseModel):
    """External runner outcome stored with the terminal event."""

    outcome: dict[str, object] = Field(default_factory=dict)


def get_runtime(request: Request) -> MemoryAgentRuntime:
    runtime: MemoryAgentRuntime | None = getattr(
        request.app.state, "agent_runtime", None
    )
    if runtime is None:  # pragma: no cover - only if lifespan was skipped
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="agent runtime is not initialised",
        )
    return runtime


RuntimeDep = Annotated[MemoryAgentRuntime, Depends(get_runtime)]


def get_local_agent(request: Request) -> LocalMemoryAgent:
    agent: LocalMemoryAgent | None = getattr(request.app.state, "local_agent", None)
    if agent is None:  # pragma: no cover - only if lifespan was skipped
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="local agent is not initialised",
        )
    return agent


LocalAgentDep = Annotated[LocalMemoryAgent, Depends(get_local_agent)]


def _runtime_error(error: Exception) -> HTTPException:
    if isinstance(error, KeyError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error))
    if isinstance(
        error,
        (
            PreviousToolFailure,
            RunConfigConflict,
            RunEventConflict,
            RunNotResumable,
            UncertainToolOutcome,
        ),
    ):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(error)
    )


@router.post("", response_model=ReplaySnapshot, status_code=status.HTTP_201_CREATED)
async def start_run(
    request: StartRunRequest, runtime: RuntimeDep
) -> ReplaySnapshot:
    try:
        run = await runtime.start_run(
            request.task,
            run_id=request.run_id,
            model_provider="local-demo",
            model="manual-tool-selection",
            prompt_version="runtime-demo.v0",
            metadata={"surface": "web-ui"},
        )
        return await runtime.replay(run.run_id)
    except Exception as error:
        raise _runtime_error(error) from error


@router.post("/auto", response_model=AgentAnswer, status_code=status.HTTP_201_CREATED)
async def run_local_agent(
    request: StartRunRequest, agent: LocalAgentDep
) -> AgentAnswer:
    try:
        return await agent.run(request.task)
    except Exception as error:
        raise _runtime_error(error) from error


@router.get("/{run_id}", response_model=ReplaySnapshot)
async def read_run(run_id: str, runtime: RuntimeDep) -> ReplaySnapshot:
    try:
        return await runtime.replay(run_id)
    except Exception as error:
        raise _runtime_error(error) from error


@router.post("/{run_id}/query", response_model=QueryToolResponse)
async def query_memory(
    run_id: str, request: QueryToolRequest, runtime: RuntimeDep
) -> QueryToolResponse:
    try:
        result = await runtime.temporal_query(
            run_id, request.tool_call_id, request.query
        )
        return QueryToolResponse(
            result=result,
            snapshot=await runtime.replay(run_id),
        )
    except Exception as error:
        raise _runtime_error(error) from error


@router.post("/{run_id}/complete", response_model=ReplaySnapshot)
async def complete_run(
    run_id: str, request: CompleteRunRequest, runtime: RuntimeDep
) -> ReplaySnapshot:
    try:
        return await runtime.complete_run(run_id, request.outcome)
    except Exception as error:
        raise _runtime_error(error) from error
