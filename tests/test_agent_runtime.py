"""Harness-level coverage for the first explicit MindBridge memory path."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from api.agent_runtime import MemoryAgentRuntime, UncertainToolOutcome
from api.models import (
    MemoryRecord,
    TemporalQueryRequest,
    TemporalQueryResult,
    UpsertPreferenceRequest,
    UpsertPreferenceResult,
)
from api.run_ledger import (
    AgentRun,
    AgentRunCreate,
    ReplaySnapshot,
    RunEvent,
    RunEventConflict,
    RunEventCreate,
    event_payload_hash,
    replay_events,
)


class _MemoryService:
    def __init__(self) -> None:
        self.query_calls = 0
        self.write_calls = 0

    async def temporal_query(
        self, request: TemporalQueryRequest
    ) -> TemporalQueryResult:
        self.query_calls += 1
        return TemporalQueryResult(
            query=request.query_string,
            hits=[],
            decay_rate_per_day=0.01,
            context_block="No matching memory.",
        )

    async def upsert_preference(
        self, request: UpsertPreferenceRequest
    ) -> UpsertPreferenceResult:
        self.write_calls += 1
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        record = MemoryRecord(
            id=7,
            content=request.content,
            namespace=request.namespace,
            category=request.category,
            created_at=now,
            valid_at=None,
            superseded_by=None,
            access_count=0,
            decay_factor=request.decay_factor,
            project=request.project,
        )
        return UpsertPreferenceResult(
            action="inserted",
            record=record,
            reason="test insert",
        )


class _MemoryLedger:
    def __init__(self) -> None:
        self.run: AgentRun | None = None
        self.events: list[RunEvent] = []
        self.fail_next_completion = False

    async def create_run(
        self, request: AgentRunCreate, *, run_id: str | None = None
    ) -> AgentRun:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.run = AgentRun(
            run_id=run_id or "run-test",
            task=request.task,
            status="running",
            next_sequence=0,
            model_provider=request.model_provider,
            model=request.model,
            prompt_version=request.prompt_version,
            tool_schema_version=request.tool_schema_version,
            budgets=request.budgets,
            metadata=request.metadata,
            created_at=now,
            updated_at=now,
            completed_at=None,
        )
        return self.run

    async def get_run(self, run_id: str) -> AgentRun:
        if self.run is None or self.run.run_id != run_id:
            raise KeyError(run_id)
        return self.run

    async def append_event(self, run_id: str, draft: RunEventCreate) -> RunEvent:
        if self.run is None:
            raise KeyError(run_id)
        existing = next(
            (event for event in self.events if event.event_id == draft.event_id), None
        )
        digest = event_payload_hash(draft.event_type, draft.payload)
        if existing is not None:
            if existing.payload_sha256 != digest:
                raise RunEventConflict(draft.event_id)
            return existing
        if self.fail_next_completion and draft.event_type == "tool_completed":
            self.fail_next_completion = False
            raise RuntimeError("simulated receipt failure")

        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        event = RunEvent(
            run_id=run_id,
            sequence=len(self.events),
            event_id=draft.event_id,
            event_type=draft.event_type,
            payload=draft.payload,
            payload_sha256=digest,
            created_at=now,
        )
        self.events.append(event)
        status = self.run.status
        if draft.event_type == "run_completed":
            status = "completed"
        elif draft.event_type == "run_failed":
            status = "failed"
        self.run = self.run.model_copy(
            update={"status": status, "next_sequence": len(self.events)}
        )
        return event

    async def replay(self, run_id: str) -> ReplaySnapshot:
        return replay_events(await self.get_run(run_id), self.events)


class AgentRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_path_is_recorded_and_completed_result_is_reused(self) -> None:
        service = _MemoryService()
        ledger = _MemoryLedger()
        runtime = MemoryAgentRuntime(service, ledger)  # type: ignore[arg-type]
        run = await runtime.start_run("Recall a project decision", run_id="run-test")
        request = TemporalQueryRequest(query_string="What did I decide?")

        first = await runtime.temporal_query(run.run_id, "call-read", request)
        retry = await runtime.temporal_query(run.run_id, "call-read", request)
        snapshot = await runtime.complete_run(run.run_id, {"answer": "none"})

        self.assertEqual(first, retry)
        self.assertEqual(service.query_calls, 1)
        self.assertEqual(
            [event.event_type for event in snapshot.events],
            [
                "run_input",
                "tool_requested",
                "tool_started",
                "tool_completed",
                "run_completed",
            ],
        )

    async def test_ambiguous_write_is_not_automatically_retried(self) -> None:
        service = _MemoryService()
        ledger = _MemoryLedger()
        runtime = MemoryAgentRuntime(service, ledger)  # type: ignore[arg-type]
        run = await runtime.start_run("Remember a preference", run_id="run-test")
        request = UpsertPreferenceRequest(
            content="Use deterministic replay for agent debugging",
            category="tool_preference",
        )
        ledger.fail_next_completion = True

        with self.assertRaisesRegex(RuntimeError, "simulated receipt failure"):
            await runtime.upsert_preference(run.run_id, "call-write", request)
        with self.assertRaises(UncertainToolOutcome):
            await runtime.upsert_preference(run.run_id, "call-write", request)

        self.assertEqual(service.write_calls, 1)
        snapshot = await ledger.replay(run.run_id)
        self.assertEqual(snapshot.pending_tool_calls, ["call-write"])


if __name__ == "__main__":
    unittest.main()
