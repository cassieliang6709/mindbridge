"""Crash-recovery guarantees for the run ledger, against a real PostgreSQL.

tests/test_run_ledger.py covers the state machine against a fake connection,
which is fast and needs no database. It cannot cover the two properties that
actually make recovery safe, because both of them live in the database:

  * the partial unique index that permits one `tool_completed` per idempotency
    key and refuses a second one
  * the interaction between a claim and the events an earlier, killed process
    already committed

So this module runs against a throwaway database it creates and drops itself.
It is skipped, not failed, when no PostgreSQL is reachable — a laptop without
Docker running should not report a red test suite for an environment problem.

    docker compose up -d db
    python -m unittest tests.test_run_ledger_recovery
"""

from __future__ import annotations

import asyncio
import os
import unittest
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from api.db import apply_schema
from api.run_ledger import (
    AgentRunCreate,
    AgentRunLedger,
    RunEventCreate,
    tool_idempotency_key,
)
from api.settings import get_settings

TEST_DB_NAME = "mindbridge_ledger_test"


def _with_database(dsn: str, database: str) -> str:
    """Point a DSN at a different database, keeping host and credentials.

    中文：保留主机与凭据，将 DSN 指向另一个数据库。
    """
    parts = urlsplit(dsn)
    return urlunsplit(parts._replace(path=f"/{database}"))


async def _server_reachable(dsn: str) -> bool:
    """Report whether the configured PostgreSQL accepts a connection.

    中文：检测配置的 PostgreSQL 是否可连接。
    """
    try:
        connection = await asyncio.wait_for(asyncpg.connect(dsn), timeout=3)
    except Exception:
        return False
    await connection.close()
    return True


class LedgerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise claim/replay/needs_review against real constraints.

    中文：在真实约束下验证 claim、replay 与 needs_review 行为。
    """

    pool: asyncpg.Pool
    admin_dsn: str
    test_dsn: str

    async def asyncSetUp(self) -> None:
        """Create a scratch database, apply the schema, open a pool.

        中文：创建临时数据库、应用 schema 并打开连接池。
        """
        settings = get_settings()
        base_dsn = os.environ.get(
            "MINDBRIDGE_TEST_DATABASE_URL", str(settings.database_url)
        )
        self.admin_dsn = _with_database(base_dsn, "postgres")
        if not await _server_reachable(self.admin_dsn):
            raise unittest.SkipTest(
                "no reachable PostgreSQL; run `docker compose up -d db`"
            )

        admin = await asyncpg.connect(self.admin_dsn)
        try:
            # Dropped first in case an earlier run was interrupted before its
            # teardown; a leftover database would otherwise carry stale events
            # into a fresh assertion.
            await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
            await admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        finally:
            await admin.close()

        self.test_dsn = _with_database(base_dsn, TEST_DB_NAME)
        pool = await asyncpg.create_pool(dsn=self.test_dsn, min_size=1, max_size=4)
        assert pool is not None
        self.pool = pool
        await apply_schema(self.pool, settings)
        self.ledger = AgentRunLedger(self.pool)

    async def asyncTearDown(self) -> None:
        """Close the pool and drop the scratch database.

        中文：关闭连接池并删除临时数据库。
        """
        await self.pool.close()
        admin = await asyncpg.connect(self.admin_dsn)
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}"')
        finally:
            await admin.close()

    async def _start_run(self, run_id: str = "run-recovery") -> str:
        """Create a run and return its id.

        中文：创建一次运行并返回其 ID。
        """
        run = await self.ledger.create_run(
            AgentRunCreate(task="write one memory", tool_schema_version="v0"),
            run_id=run_id,
        )
        return run.run_id

    async def _append(
        self,
        run_id: str,
        event_id: str,
        event_type: str,
        payload: dict[str, object],
        key: str | None,
    ) -> None:
        """Append one event, keeping the call sites short.

        中文：追加一条事件，简化调用处代码。
        """
        await self.ledger.append_event(
            run_id,
            RunEventCreate(
                event_id=event_id,
                event_type=event_type,  # type: ignore[arg-type]
                payload=payload,
                idempotency_key=key,
            ),
        )

    # --- the identity itself ---------------------------------------------

    def test_key_ignores_argument_order_but_not_schema_version(self) -> None:
        """Argument order must not change identity; a schema bump must.

        中文：参数顺序不改变身份，schema 版本变化则必须改变身份。
        """
        first = tool_idempotency_key(
            "run-1", "upsert_preference", "v0", {"content": "用 uv", "category": "tool"}
        )
        reordered = tool_idempotency_key(
            "run-1", "upsert_preference", "v0", {"category": "tool", "content": "用 uv"}
        )
        bumped = tool_idempotency_key(
            "run-1", "upsert_preference", "v1", {"content": "用 uv", "category": "tool"}
        )
        other_run = tool_idempotency_key(
            "run-2", "upsert_preference", "v0", {"content": "用 uv", "category": "tool"}
        )
        self.assertEqual(first, reordered)
        self.assertNotEqual(first, bumped)
        self.assertNotEqual(first, other_run)

    # --- the three verdicts ----------------------------------------------

    async def test_first_call_executes(self) -> None:
        """A call with no history is allowed to run.

        中文：没有历史记录的调用允许执行。
        """
        run_id = await self._start_run()
        claim = await self.ledger.claim_tool_call(
            run_id, "upsert_preference", {"content": "prefers uv"}
        )
        self.assertEqual(claim.decision, "execute")
        self.assertEqual(claim.attempt, 1)

    async def test_completed_call_replays_the_recorded_result(self) -> None:
        """A completed call returns its result and must not run again.

        中文：已完成的调用返回记录结果，且不得再次执行。
        """
        run_id = await self._start_run()
        arguments = {"content": "prefers uv"}
        key = tool_idempotency_key(run_id, "upsert_preference", "v0", arguments)

        await self._append(run_id, "e1", "tool_requested", {"tool_call_id": "c1"}, key)
        await self._append(run_id, "e2", "tool_started", {"tool_call_id": "c1"}, key)
        await self._append(
            run_id,
            "e3",
            "tool_completed",
            {"tool_call_id": "c1", "result": "inserted: [42]"},
            key,
        )

        claim = await self.ledger.claim_tool_call(
            run_id, "upsert_preference", arguments
        )
        self.assertEqual(claim.decision, "replay")
        self.assertEqual(claim.result, "inserted: [42]")

    async def test_failed_call_may_be_retried(self) -> None:
        """A recorded failure means the effect did not land, so retry is safe.

        中文：已记录的失败意味着副作用未发生，可以重试。
        """
        run_id = await self._start_run()
        arguments = {"content": "prefers uv"}
        key = tool_idempotency_key(run_id, "upsert_preference", "v0", arguments)

        await self._append(run_id, "e1", "tool_requested", {"tool_call_id": "c1"}, key)
        await self._append(run_id, "e2", "tool_started", {"tool_call_id": "c1"}, key)
        await self._append(
            run_id, "e3", "tool_failed", {"tool_call_id": "c1", "error": "timeout"}, key
        )

        claim = await self.ledger.claim_tool_call(
            run_id, "upsert_preference", arguments
        )
        self.assertEqual(claim.decision, "execute")
        self.assertEqual(claim.attempt, 2)

    async def test_interrupted_call_is_parked_unless_the_tool_is_retry_safe(
        self,
    ) -> None:
        """The undecidable case: started, never resolved.

        中文：不可判定情形——已开始但没有终态。

        The ledger cannot tell whether the side effect landed, so the verdict
        depends entirely on what the tool declared about itself.
        """
        run_id = await self._start_run()
        arguments = {"content": "prefers uv"}
        key = tool_idempotency_key(run_id, "upsert_preference", "v0", arguments)

        await self._append(run_id, "e1", "tool_requested", {"tool_call_id": "c1"}, key)
        await self._append(run_id, "e2", "tool_started", {"tool_call_id": "c1"}, key)
        # and then the process died: no tool_completed, no tool_failed.

        parked = await self.ledger.claim_tool_call(
            run_id, "upsert_preference", arguments, retry_safe=False
        )
        self.assertEqual(parked.decision, "needs_review")

        retried = await self.ledger.claim_tool_call(
            run_id, "upsert_preference", arguments, retry_safe=True
        )
        self.assertEqual(retried.decision, "execute")
        self.assertEqual(retried.attempt, 2)

    # --- the database-level backstop --------------------------------------

    async def test_second_completion_of_one_key_is_refused_by_postgres(self) -> None:
        """Two completions for one key must be impossible, not merely unlikely.

        中文：同一幂等键出现两次完成事件必须在数据库层被拒绝。

        This is the assertion the fake-connection tests cannot make. If a future
        bug lets a completed call execute again, the second receipt hits the
        partial unique index and raises, instead of quietly recording a second
        write to the user's memory.
        """
        run_id = await self._start_run()
        key = tool_idempotency_key(
            run_id, "upsert_preference", "v0", {"content": "prefers uv"}
        )

        await self._append(run_id, "e1", "tool_requested", {"tool_call_id": "c1"}, key)
        await self._append(run_id, "e2", "tool_started", {"tool_call_id": "c1"}, key)
        await self._append(
            run_id, "e3", "tool_completed", {"tool_call_id": "c1", "result": "ok"}, key
        )

        # A different event id and a different tool_call_id: everything the
        # ledger checks for duplicate *events* passes. Only the idempotency key
        # is the same, and that alone must be enough to stop it.
        await self._append(run_id, "e4", "tool_requested", {"tool_call_id": "c2"}, key)
        await self._append(run_id, "e5", "tool_started", {"tool_call_id": "c2"}, key)
        with self.assertRaises(asyncpg.UniqueViolationError):
            await self._append(
                run_id,
                "e6",
                "tool_completed",
                {"tool_call_id": "c2", "result": "ok again"},
                key,
            )

    async def test_replay_separates_pending_from_needs_review(self) -> None:
        """A parked call is not pending: resuming will never clear it.

        中文：needs_review 的调用不属于 pending，重启也无法清除。
        """
        run_id = await self._start_run()
        key = tool_idempotency_key(run_id, "send_report", "v0", {"to": "team"})

        await self._append(run_id, "e1", "tool_requested", {"tool_call_id": "c1"}, key)
        await self._append(run_id, "e2", "tool_started", {"tool_call_id": "c1"}, key)
        await self._append(
            run_id,
            "e3",
            "tool_needs_review",
            {"tool_call_id": "c1", "reason": "interrupted, not retry_safe"},
            key,
        )

        snapshot = await self.ledger.replay(run_id)
        self.assertEqual(snapshot.pending_tool_calls, [])
        self.assertEqual(snapshot.needs_review_tool_calls, ["c1"])


if __name__ == "__main__":
    unittest.main()
