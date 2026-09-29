"""The first explicit MindBridge harness path over memory read/write tools.

This module owns orchestration only: durable tool lifecycle events, safe retry
rules, and typed results. Model selection and prompting remain outside v0.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from pydantic import BaseModel

from .models import (
    TemporalQueryRequest,
    TemporalQueryResult,
    UpsertPreferenceRequest,
    UpsertPreferenceResult,
)
from .run_ledger import (
    AgentRun,
    AgentRunCreate,
    AgentRunLedger,
    ReplaySnapshot,
    RunEvent,
    RunEventCreate,
    ResumePoint,
)
from .service import MemoryService

_ResultT = TypeVar("_ResultT", bound=BaseModel)


class UncertainToolOutcome(RuntimeError):
    """A write may have succeeded, so automatic retry would be unsafe."""


class PreviousToolFailure(RuntimeError):
    """A failed tool call needs a new call id for an explicit retry."""


class MemoryAgentRuntime:
    """Record and execute one traceable MindBridge memory-tool path."""

    def __init__(
        self,
        service: MemoryService,
        ledger: AgentRunLedger,
        *,
        tool_schema_version: str = "mindbridge-memory.v0",
    ) -> None:
        self._service = service
        self._ledger = ledger
        self._tool_schema_version = tool_schema_version

    async def start_run(
        self,
        task: str,
        *,
        run_id: str | None = None,
        model_provider: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        budgets: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> AgentRun:
        """Create a run and persist its original user task as event zero."""

        run = await self._ledger.create_run(
            AgentRunCreate(
                task=task,
                model_provider=model_provider,
                model=model,
                prompt_version=prompt_version,
                tool_schema_version=self._tool_schema_version,
                budgets=budgets or {},
                metadata=metadata or {},
            ),
            run_id=run_id,
        )
        await self._ledger.append_event(
            run.run_id,
            RunEventCreate(
                event_id=f"{run.run_id}:input",
                event_type="run_input",
                payload={"task": task},
            ),
        )
        return await self._ledger.get_run(run.run_id)

    async def temporal_query(
        self,
        run_id: str,
        tool_call_id: str,
        request: TemporalQueryRequest,
    ) -> TemporalQueryResult:
        """Execute or safely resume one read-only temporal-memory query."""

        snapshot = await self._ledger.replay(run_id)
        terminal = _tool_terminal_event(snapshot, tool_call_id)
        if terminal is not None:
            if terminal.event_type == "tool_failed":
                raise PreviousToolFailure(
                    f"tool call {tool_call_id} failed; retry with a new call id"
                )
            return TemporalQueryResult.model_validate(terminal.payload["result"])

        return await self._execute_tool(
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name="temporal_query",
            risk="read",
            arguments=request.model_dump(mode="json"),
            execute=lambda: self._service.temporal_query(request),
            parse_result=TemporalQueryResult.model_validate,
        )

    async def upsert_preference(
        self,
        run_id: str,
        tool_call_id: str,
        request: UpsertPreferenceRequest,
    ) -> UpsertPreferenceResult:
        """Execute one write, refusing an ambiguous post-crash retry."""

        snapshot = await self._ledger.replay(run_id)
        terminal = _tool_terminal_event(snapshot, tool_call_id)
        if terminal is not None:
            if terminal.event_type == "tool_failed":
                raise PreviousToolFailure(
                    f"tool call {tool_call_id} failed; retry with a new call id"
                )
            return UpsertPreferenceResult.model_validate(terminal.payload["result"])

        if _tool_was_started(snapshot, tool_call_id):
            raise UncertainToolOutcome(
                f"write tool {tool_call_id} started without a durable result; "
                "inspect the memory state before choosing a new call id"
            )

        return await self._execute_tool(
            run_id=run_id,
            tool_call_id=tool_call_id,
            tool_name="upsert_preference",
            risk="write",
            arguments=request.model_dump(mode="json"),
            execute=lambda: self._service.upsert_preference(request),
            parse_result=UpsertPreferenceResult.model_validate,
        )

    async def complete_run(
        self, run_id: str, outcome: dict[str, object]
    ) -> ReplaySnapshot:
        """Close a run after its external runner has produced an outcome."""

        snapshot = await self._ledger.replay(run_id)
        if snapshot.pending_tool_calls:
            raise UncertainToolOutcome(
                "cannot complete a run with pending tool calls: "
                + ", ".join(snapshot.pending_tool_calls)
            )
        await self._ledger.append_event(
            run_id,
            RunEventCreate(
                event_id=f"{run_id}:completed",
                event_type="run_completed",
                payload={"outcome": outcome},
            ),
        )
        return await self._ledger.replay(run_id)

    async def replay(self, run_id: str) -> ReplaySnapshot:
        """Return the verified, persisted trace for one run."""

        return await self._ledger.replay(run_id)

    async def resume_point(self, run_id: str) -> ResumePoint:
        """Return the durable boundary from which a runner can continue."""

        return await self._ledger.resume_point(run_id)

    async def _execute_tool(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        tool_name: str,
        risk: Literal["read", "write"],
        arguments: dict[str, object],
        execute: Callable[[], Awaitable[_ResultT]],
        parse_result: Callable[[object], _ResultT],
    ) -> _ResultT:
        requested_id = f"{tool_call_id}:requested"
        started_id = f"{tool_call_id}:started"
        await self._ledger.append_event(
            run_id,
            RunEventCreate(
                event_id=requested_id,
                event_type="tool_requested",
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "tool_version": self._tool_schema_version,
                    "risk": risk,
                    "arguments": arguments,
                },
            ),
        )
        await self._ledger.append_event(
            run_id,
            RunEventCreate(
                event_id=started_id,
                event_type="tool_started",
                payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
            ),
        )

        try:
            result = await execute()
        except Exception as error:
            await self._ledger.append_event(
                run_id,
                RunEventCreate(
                    event_id=f"{tool_call_id}:failed",
                    event_type="tool_failed",
                    payload={
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "error_type": type(error).__name__,
                        "error": str(error),
                    },
                ),
            )
            raise

        parsed = parse_result(result)
        result_payload = parsed.model_dump(mode="json")
        # Deliberately outside the executor exception block. If this append
        # fails after a write succeeds, the ledger remains at tool_started and
        # upsert_preference refuses to guess whether the side effect happened.
        await self._ledger.append_event(
            run_id,
            RunEventCreate(
                event_id=f"{tool_call_id}:completed",
                event_type="tool_completed",
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "result": result_payload,
                },
            ),
        )
        return parsed


def _tool_terminal_event(
    snapshot: ReplaySnapshot, tool_call_id: str
) -> RunEvent | None:
    for event in reversed(snapshot.events):
        if (
            event.event_type in {"tool_completed", "tool_failed"}
            and event.payload.get("tool_call_id") == tool_call_id
        ):
            return event
    return None


def _tool_was_started(snapshot: ReplaySnapshot, tool_call_id: str) -> bool:
    return any(
        event.event_type == "tool_started"
        and event.payload.get("tool_call_id") == tool_call_id
        for event in snapshot.events
    )
