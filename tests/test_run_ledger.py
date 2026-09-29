"""Deterministic replay and idempotency coverage for Agent Runtime v0."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from api.run_ledger import (
    AgentRun,
    AgentRunCreate,
    AgentRunLedger,
    RunConfigConflict,
    RunEvent,
    RunEventConflict,
    RunEventCreate,
    RunNotResumable,
    _event_from_row,
    _run_from_row,
    event_payload_hash,
    replay_events,
)


class _FakeTransaction:
    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.runs: dict[str, dict[str, object]] = {}
        self.events: dict[tuple[str, str], dict[str, object]] = {}

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        normalized = " ".join(sql.split())
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)

        if normalized.startswith("INSERT INTO agent_runtime_runs"):
            run_id = str(args[0])
            if run_id in self.runs:
                return None
            row = {
                "run_id": run_id,
                "task": args[1],
                "status": "running",
                "next_sequence": 0,
                "model_provider": args[2],
                "model": args[3],
                "prompt_version": args[4],
                "tool_schema_version": args[5],
                "budgets": args[6],
                "metadata": args[7],
                "created_at": now,
                "updated_at": now,
                "completed_at": None,
            }
            self.runs[run_id] = row
            return row

        if normalized.startswith("SELECT status, next_sequence FROM agent_runtime_runs"):
            row = self.runs.get(str(args[0]))
            if row is None:
                return None
            return {"status": row["status"], "next_sequence": row["next_sequence"]}

        if "FROM agent_runtime_events" in normalized and "event_id = $2" in normalized:
            return self.events.get((str(args[0]), str(args[1])))

        if normalized.startswith("INSERT INTO agent_runtime_events"):
            run_id = str(args[0])
            row = {
                "run_id": run_id,
                "sequence": args[1],
                "event_id": args[2],
                "event_type": args[3],
                "payload": args[4],
                "payload_sha256": args[5],
                "created_at": now,
            }
            self.events[(run_id, str(args[2]))] = row
            return row

        if "FROM agent_runtime_runs WHERE run_id = $1" in normalized:
            return self.runs.get(str(args[0]))

        raise AssertionError(f"unexpected fetchrow SQL: {normalized}")

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        normalized = " ".join(sql.split())
        if "FROM agent_runtime_events" not in normalized:
            raise AssertionError(f"unexpected fetch SQL: {normalized}")
        run_id = str(args[0])
        return sorted(
            (
                row
                for (stored_run_id, _), row in self.events.items()
                if stored_run_id == run_id
            ),
            key=lambda row: int(row["sequence"]),
        )

    async def execute(self, sql: str, *args: object) -> None:
        normalized = " ".join(sql.split())
        if not normalized.startswith("UPDATE agent_runtime_runs"):
            raise AssertionError(f"unexpected execute SQL: {normalized}")
        row = self.runs[str(args[0])]
        row["next_sequence"] = args[1]
        row["status"] = args[2]
        row["updated_at"] = datetime(2026, 9, 1, tzinfo=timezone.utc)
        if args[2] != "running":
            row["completed_at"] = row["updated_at"]


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.connection)

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object] | None:
        return await self.connection.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        return await self.connection.fetch(sql, *args)


def _run(*, status: str = "running", next_sequence: int = 0) -> AgentRun:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return AgentRun(
        run_id="run-test",
        task="Recall and update one project decision",
        status=status,
        next_sequence=next_sequence,
        model_provider="test",
        model="fixed",
        prompt_version="v0",
        tool_schema_version="v0",
        budgets={},
        metadata={},
        created_at=now,
        updated_at=now,
        completed_at=now if status != "running" else None,
    )


def _event(sequence: int, event_type: str, payload: dict[str, object]) -> RunEvent:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return RunEvent(
        run_id="run-test",
        sequence=sequence,
        event_id=f"event-{sequence}",
        event_type=event_type,
        payload=payload,
        payload_sha256=event_payload_hash(event_type, payload),
        created_at=now,
    )


class RunLedgerReplayTests(unittest.TestCase):
    def test_asyncpg_json_text_is_normalized_at_the_storage_boundary(self) -> None:
        now = datetime(2026, 9, 1, tzinfo=timezone.utc)
        run = _run_from_row(
            {
                **_run().model_dump(),
                "budgets": '{"max_turns": 8}',
                "metadata": '{"source": "test"}',
            }
        )
        event = _event_from_row(
            {
                **_event(0, "checkpoint", {}).model_dump(),
                "payload": '{"state": "saved"}',
                "created_at": now,
            }
        )

        self.assertEqual(run.budgets, {"max_turns": 8})
        self.assertEqual(run.metadata, {"source": "test"})
        self.assertEqual(event.payload, {"state": "saved"})

    def test_canonical_hash_ignores_mapping_order(self) -> None:
        first = event_payload_hash("checkpoint", {"b": 2, "a": 1})
        second = event_payload_hash("checkpoint", {"a": 1, "b": 2})
        self.assertEqual(first, second)

    def test_replay_derives_pending_tool_and_resume_position(self) -> None:
        events = [
            _event(0, "run_input", {"task": "remember this"}),
            _event(1, "tool_requested", {"tool_call_id": "call-1"}),
            _event(2, "tool_started", {"tool_call_id": "call-1"}),
        ]

        snapshot = replay_events(_run(next_sequence=3), events)

        self.assertEqual(snapshot.next_sequence, 3)
        self.assertEqual(snapshot.pending_tool_calls, ["call-1"])

    def test_completed_tool_is_not_pending(self) -> None:
        events = [
            _event(0, "tool_requested", {"tool_call_id": "call-1"}),
            _event(1, "tool_started", {"tool_call_id": "call-1"}),
            _event(2, "tool_completed", {"tool_call_id": "call-1"}),
            _event(3, "run_completed", {"outcome": "ok"}),
        ]

        snapshot = replay_events(
            _run(status="completed", next_sequence=4), events
        )

        self.assertEqual(snapshot.pending_tool_calls, [])

    def test_tool_cannot_complete_before_it_starts(self) -> None:
        events = [
            _event(0, "tool_requested", {"tool_call_id": "call-1"}),
            _event(1, "tool_completed", {"tool_call_id": "call-1"}),
        ]
        with self.assertRaisesRegex(ValueError, "cannot transition"):
            replay_events(_run(next_sequence=2), events)

    def test_replay_rejects_sequence_gap(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected sequence 1"):
            replay_events(
                _run(next_sequence=2),
                [
                    _event(0, "run_input", {}),
                    _event(2, "checkpoint", {}),
                ],
            )

    def test_replay_rejects_payload_tampering(self) -> None:
        event = _event(0, "checkpoint", {"state": "before"})
        tampered = event.model_copy(update={"payload": {"state": "after"}})
        with self.assertRaisesRegex(ValueError, "payload hash mismatch"):
            replay_events(_run(next_sequence=1), [tampered])

    def test_replay_rejects_missing_tool_call_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "needs tool_call_id"):
            replay_events(
                _run(next_sequence=1), [_event(0, "tool_started", {})]
            )

    def test_terminal_event_must_match_run_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            replay_events(
                _run(status="failed", next_sequence=1),
                [_event(0, "run_completed", {})],
            )

    def test_resume_error_has_specific_type(self) -> None:
        error = RunNotResumable("already completed")
        self.assertIsInstance(error, RuntimeError)


class RunLedgerPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_creation_retry_requires_identical_configuration(self) -> None:
        pool = _FakePool()
        ledger = AgentRunLedger(pool)  # type: ignore[arg-type]
        request = AgentRunCreate(
            task="Recall a decision",
            model="fixed-model",
            budgets={"max_turns": 8},
        )

        first = await ledger.create_run(request, run_id="run-test")
        retry = await ledger.create_run(request, run_id="run-test")

        self.assertEqual(first, retry)
        with self.assertRaises(RunConfigConflict):
            await ledger.create_run(
                request.model_copy(update={"task": "A different task"}),
                run_id="run-test",
            )

    async def test_event_retry_is_idempotent_and_conflicts_are_rejected(self) -> None:
        pool = _FakePool()
        ledger = AgentRunLedger(pool)  # type: ignore[arg-type]
        await ledger.create_run(
            AgentRunCreate(task="Recall a decision"), run_id="run-test"
        )
        draft = RunEventCreate(
            event_id="input-1",
            event_type="run_input",
            payload={"task": "Recall a decision"},
        )

        first = await ledger.append_event("run-test", draft)
        retry = await ledger.append_event("run-test", draft)

        self.assertEqual(first, retry)
        self.assertEqual(len(pool.connection.events), 1)
        self.assertEqual(pool.connection.runs["run-test"]["next_sequence"], 1)

        with self.assertRaises(RunEventConflict):
            await ledger.append_event(
                "run-test",
                draft.model_copy(update={"payload": {"task": "changed"}}),
            )

    async def test_terminal_run_rejects_new_events_but_accepts_exact_retry(self) -> None:
        pool = _FakePool()
        ledger = AgentRunLedger(pool)  # type: ignore[arg-type]
        await ledger.create_run(AgentRunCreate(task="Finish"), run_id="run-test")
        terminal = RunEventCreate(
            event_id="done-1", event_type="run_completed", payload={}
        )
        stored = await ledger.append_event("run-test", terminal)

        self.assertEqual(await ledger.append_event("run-test", terminal), stored)
        self.assertEqual((await ledger.list_events("run-test"))[0], stored)
        with self.assertRaises(RunNotResumable):
            await ledger.append_event(
                "run-test",
                RunEventCreate(
                    event_id="late-1", event_type="checkpoint", payload={}
                ),
            )


if __name__ == "__main__":
    unittest.main()
