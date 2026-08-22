"""Regression coverage for project-scoped memory edits."""

import unittest
from datetime import datetime, timezone

from api.memory.vector_store import VectorMemoryStore
from api.models import MemoryRecord


class _FakeTransaction:
    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakeConnection:
    def __init__(self, old_row: dict, replacement_row: dict) -> None:
        self._old_row = old_row
        self._replacement_row = replacement_row
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    async def fetchrow(self, sql: str, *args: object) -> dict:
        self.fetchrow_calls.append((sql, args))
        if sql.lstrip().startswith("SELECT"):
            return self._old_row
        if sql.lstrip().startswith("INSERT"):
            return self._replacement_row
        raise AssertionError(f"unexpected fetchrow SQL: {sql}")

    async def execute(self, sql: str, *args: object) -> None:
        self.execute_calls.append((sql, args))


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        return None


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._connection)


class MemoryEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_preserves_project_scope_on_replacement(self) -> None:
        created_at = datetime(2026, 8, 21, tzinfo=timezone.utc)
        old_record = MemoryRecord(
            id=41,
            content="Use uv for this project",
            namespace="operational",
            category="tool_preference",
            created_at=created_at,
            valid_at=None,
            superseded_by=None,
            access_count=0,
            decay_factor=1.25,
            project="mindbridge",
        )
        replacement_record = old_record.model_copy(
            update={"id": 42, "content": "Use uv for MindBridge"}
        )
        connection = _FakeConnection(
            old_record.model_dump(), replacement_record.model_dump()
        )
        store = VectorMemoryStore(
            _FakePool(connection),
            decay_rate_per_day=0.01,
            dedup_threshold=0.8,
            superseded_penalty=0.5,
        )

        result = await store.edit(
            memory_id=old_record.id,
            content=replacement_record.content,
            embedding=[0.1, 0.2],
        )

        insert_sql, insert_args = next(
            (sql, args)
            for sql, args in connection.fetchrow_calls
            if sql.lstrip().startswith("INSERT")
        )
        self.assertRegex(
            insert_sql,
            r"\(\s*content,\s*namespace,\s*category,\s*embedding,\s*decay_factor,\s*project\s*\)",
        )
        self.assertEqual(insert_args[5], old_record.project)
        self.assertEqual(result.project, old_record.project)
        self.assertEqual(insert_args[4], old_record.decay_factor)


if __name__ == "__main__":
    unittest.main()
