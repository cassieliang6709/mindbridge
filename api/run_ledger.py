"""Append-only, replayable state for the MindBridge agent harness.

The ledger records decisions and observations; it does not execute model or
tool calls. That boundary is intentional: tracing is useful on its own, and a
component that both records and executes cannot be trusted to record honestly
about its own failures.

What the ledger does own is the *identity* of a tool call. `tool_idempotency_key`
hashes the run, the tool name, the tool's schema version and its canonical
arguments into one key, and `claim_tool_call` answers the only question that
matters before executing: has this exact call already happened?

Three answers, and they are different problems:

  replay       — a `tool_completed` event carries this key. The work is done and
                 the result was recorded. Return it; the tool must not run.
  execute      — nothing, or a recorded failure. Running it is correct.
  needs_review — a `tool_started` with no completion. The process died somewhere
                 between the claim and the receipt, so the side effect may or
                 may not have landed. **Nothing in the ledger can distinguish
                 those two worlds.** There is no clever recovery here, only a
                 policy: a tool that has declared `retry_safe` re-runs, and
                 everything else stops for a human. A runtime that silently
                 retries a non-idempotent tool is not recovering, it is
                 duplicating.

`retry_safe` is a claim about the tool, not about the ledger. MindBridge's
memory write earns it because `upsert_preference` deduplicates by content
cosine before inserting, so a second identical write refreshes an existing row
instead of adding one. A tool that posts a message or charges a card does not.

中文：账本只记录，不执行。它拥有的是工具调用的身份：把 run、工具名、工具
schema 版本与规范化参数哈希为幂等键，执行前用 `claim_tool_call` 判定该调用
是否已经发生。已完成则直接返回记录结果；失败可重试；`started` 却没有终态
属于不可判定情形，只有声明 `retry_safe` 的工具才重跑，其余交人工处理。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Literal
from uuid import uuid4

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["running", "completed", "failed"]
RunEventType = Literal[
    "run_input",
    "model_requested",
    "model_completed",
    "tool_requested",
    "tool_approved",
    "tool_started",
    "tool_completed",
    "tool_failed",
    "tool_needs_review",
    "checkpoint",
    "run_completed",
    "run_failed",
]

ToolClaimDecision = Literal["execute", "replay", "needs_review"]

# Every event that belongs to one logical tool call and therefore carries the
# idempotency key. Kept in one place so the state machine below and the claim
# lookup cannot drift apart.
_TOOL_EVENTS: frozenset[str] = frozenset(
    {
        "tool_requested",
        "tool_approved",
        "tool_started",
        "tool_completed",
        "tool_failed",
        "tool_needs_review",
    }
)

_TERMINAL_EVENT_STATUS: dict[RunEventType, RunStatus] = {
    "run_completed": "completed",
    "run_failed": "failed",
}

_RUN_COLUMNS = """
    run_id, task, status, next_sequence, model_provider, model,
    prompt_version, tool_schema_version, budgets, metadata,
    created_at, updated_at, completed_at
"""

_EVENT_COLUMNS = """
    run_id, sequence, event_id, event_type, payload, payload_sha256,
    idempotency_key, created_at
"""


class RunEventConflict(RuntimeError):
    """An event id was reused with different immutable content."""


class RunConfigConflict(RuntimeError):
    """A run id was reused with different immutable configuration."""


class RunNotResumable(RuntimeError):
    """A terminal run cannot accept new events or be resumed."""


class AgentRunCreate(BaseModel):
    """Immutable configuration captured when one agent run starts."""

    task: str = Field(min_length=1)
    model_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    budgets: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)


class AgentRun(BaseModel):
    """Persisted run metadata and its next append position."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    task: str
    status: RunStatus
    next_sequence: int
    model_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    tool_schema_version: str | None = None
    budgets: dict[str, object]
    metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None


class RunEventCreate(BaseModel):
    """One immutable event proposed for append to a run."""

    event_id: str = Field(min_length=1)
    event_type: RunEventType
    payload: dict[str, object] = Field(default_factory=dict)
    # Set on the tool_* events of one logical call; NULL elsewhere. Part of the
    # event's immutable content, so a retry that supplies a different key is a
    # conflict rather than a silent overwrite.
    idempotency_key: str | None = None


class RunEvent(BaseModel):
    """One sequence-numbered event in the run ledger."""

    model_config = ConfigDict(from_attributes=True)

    run_id: str
    sequence: int
    event_id: str
    event_type: RunEventType
    payload: dict[str, object]
    payload_sha256: str
    idempotency_key: str | None = None
    created_at: datetime


class ToolClaim(BaseModel):
    """The verdict on whether one tool call may execute.

    中文：某次工具调用是否可以执行的判定结果。

    Attributes:
        decision: execute, replay, or needs_review.
        idempotency_key: Identity of this call; pass it on the tool_* events.
        attempt: 1 for a first execution, incremented after a recorded failure.
        result: The recorded result, present only when decision is "replay".
        reason: Why this verdict, in words a trace reader can act on.
    """

    decision: ToolClaimDecision
    idempotency_key: str
    attempt: int
    result: object | None = None
    reason: str


class ReplaySnapshot(BaseModel):
    """Verified deterministic projection of one stored run."""

    run: AgentRun
    events: list[RunEvent]
    next_sequence: int
    pending_tool_calls: list[str]
    # Calls that were interrupted mid-flight on a tool that cannot be retried
    # automatically. Separate from pending because no amount of resuming will
    # clear them — they are waiting on a person, not on the runtime.
    needs_review_tool_calls: list[str] = Field(default_factory=list)


class ResumePoint(BaseModel):
    """The durable boundary from which an external runner may continue."""

    run_id: str
    next_sequence: int
    last_event: RunEvent | None
    pending_tool_calls: list[str]


def _json_object(value: object) -> dict[str, object]:
    """Normalize asyncpg JSONB output whether a codec returns text or a dict."""

    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, Mapping):
        raise ValueError("run ledger JSONB value must be an object")
    return dict(decoded)


def _run_from_row(row: Mapping[str, object]) -> AgentRun:
    data = dict(row)
    data["budgets"] = _json_object(data["budgets"])
    data["metadata"] = _json_object(data["metadata"])
    return AgentRun(**data)


def _event_from_row(row: Mapping[str, object]) -> RunEvent:
    data = dict(row)
    data["payload"] = _json_object(data["payload"])
    return RunEvent(**data)


def event_payload_hash(event_type: RunEventType, payload: dict[str, object]) -> str:
    """Hash canonical JSON so replay detects changed type or payload content."""

    canonical = json.dumps(
        {"event_type": event_type, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def tool_idempotency_key(
    run_id: str,
    tool_name: str,
    tool_schema_version: str,
    arguments: dict[str, object],
) -> str:
    """Derive the identity of one tool call inside one run.

    中文：为一次工具调用生成运行内唯一的身份键。

    The arguments are canonicalised — sorted keys, no incidental whitespace,
    non-ASCII left as itself — because key order must not change the identity of
    a call. A retry that happened to serialise its arguments differently would
    otherwise hash to a new key, miss the recorded completion, and run the side
    effect a second time. `ensure_ascii=False` matters here specifically: these
    arguments routinely carry Chinese memory content.

    `tool_schema_version` is part of the identity rather than metadata. A tool
    whose arguments changed shape is not the same tool for replay purposes, and
    replaying an old result into a new schema is worse than re-running.

    Args:
        run_id: Run the call belongs to. Keys are scoped to a run, so the same
            call in a different run is a different call.
        tool_name: Registered tool name.
        tool_schema_version: Version of the argument schema that validated it.
        arguments: The validated arguments as passed to the tool.

    Returns:
        A hex sha256 digest.
    """
    canonical = json.dumps(
        {
            "run_id": run_id,
            "tool": tool_name,
            "tool_schema_version": tool_schema_version,
            "arguments": arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def replay_events(run: AgentRun, events: list[RunEvent]) -> ReplaySnapshot:
    """Validate event order and hashes, then derive the resumable tool state."""

    tool_states: dict[str, str] = {}
    terminal_status: RunStatus | None = None

    for expected_sequence, event in enumerate(events):
        if event.run_id != run.run_id:
            raise ValueError(
                f"event {event.event_id} belongs to {event.run_id}, not {run.run_id}"
            )
        if event.sequence != expected_sequence:
            raise ValueError(
                f"run {run.run_id} expected sequence {expected_sequence}, "
                f"found {event.sequence}"
            )
        expected_hash = event_payload_hash(event.event_type, event.payload)
        if event.payload_sha256 != expected_hash:
            raise ValueError(f"event {event.event_id} payload hash mismatch")

        tool_call_id = event.payload.get("tool_call_id")
        if event.event_type in _TOOL_EVENTS:
            if not isinstance(tool_call_id, str) or not tool_call_id:
                raise ValueError(f"event {event.event_id} needs tool_call_id")

            current_state = tool_states.get(tool_call_id)
            allowed_previous: dict[str, set[str | None]] = {
                "tool_requested": {None},
                "tool_approved": {"requested"},
                # "failed" is allowed as a predecessor because a retry after a
                # recorded failure is a legitimate second attempt: the ledger
                # knows the effect did not land. "completed" is not, and that
                # asymmetry is the whole guarantee.
                "tool_started": {"requested", "approved", "failed"},
                "tool_completed": {"started"},
                "tool_failed": {"started"},
                "tool_needs_review": {"started"},
            }
            if current_state not in allowed_previous[event.event_type]:
                raise ValueError(
                    f"tool call {tool_call_id} cannot transition from "
                    f"{current_state!r} via {event.event_type}"
                )
            tool_states[tool_call_id] = event.event_type.removeprefix("tool_")

        event_terminal_status = _TERMINAL_EVENT_STATUS.get(event.event_type)
        if event_terminal_status is not None:
            if expected_sequence != len(events) - 1:
                raise ValueError("terminal event must be the last event")
            terminal_status = event_terminal_status

    if run.next_sequence != len(events):
        raise ValueError(
            f"run {run.run_id} next_sequence={run.next_sequence}, "
            f"but ledger contains {len(events)} event(s)"
        )
    if run.status == "running" and terminal_status is not None:
        raise ValueError("running run contains a terminal event")
    if run.status != "running" and terminal_status != run.status:
        raise ValueError(
            f"run status {run.status} does not match terminal event {terminal_status}"
        )

    return ReplaySnapshot(
        run=run,
        events=events,
        next_sequence=len(events),
        pending_tool_calls=sorted(
            tool_call_id
            for tool_call_id, state in tool_states.items()
            if state not in {"completed", "failed", "needs_review"}
        ),
        needs_review_tool_calls=sorted(
            tool_call_id
            for tool_call_id, state in tool_states.items()
            if state == "needs_review"
        ),
    )


class AgentRunLedger:
    """Persist and verify append-only agent run events in PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_run(
        self, request: AgentRunCreate, *, run_id: str | None = None
    ) -> AgentRun:
        """Create one run with immutable model/tool configuration."""

        stable_run_id = run_id or f"run_{uuid4().hex}"
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO agent_runtime_runs
                (run_id, task, model_provider, model, prompt_version,
                 tool_schema_version, budgets, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            ON CONFLICT (run_id) DO NOTHING
            RETURNING {_RUN_COLUMNS}
            """,
            stable_run_id,
            request.task,
            request.model_provider,
            request.model,
            request.prompt_version,
            request.tool_schema_version,
            json.dumps(request.budgets, sort_keys=True),
            json.dumps(request.metadata, sort_keys=True),
        )
        if row is not None:
            return _run_from_row(row)

        existing = await self.get_run(stable_run_id)
        immutable = {
            "task": request.task,
            "model_provider": request.model_provider,
            "model": request.model,
            "prompt_version": request.prompt_version,
            "tool_schema_version": request.tool_schema_version,
            "budgets": request.budgets,
            "metadata": request.metadata,
        }
        if any(getattr(existing, key) != value for key, value in immutable.items()):
            raise RunConfigConflict(
                f"agent run {stable_run_id} already exists with different "
                "immutable configuration"
            )
        return existing

    async def get_run(self, run_id: str) -> AgentRun:
        """Read one run without changing its state."""

        row = await self._pool.fetchrow(
            f"SELECT {_RUN_COLUMNS} FROM agent_runtime_runs WHERE run_id = $1", run_id
        )
        if row is None:
            raise KeyError(f"agent run {run_id} not found")
        return _run_from_row(row)

    async def append_event(self, run_id: str, draft: RunEventCreate) -> RunEvent:
        """Append once; an identical event id is safe to retry."""

        digest = event_payload_hash(draft.event_type, draft.payload)
        async with self._pool.acquire() as connection:
            async with connection.transaction():
                run_row = await connection.fetchrow(
                    """
                    SELECT status, next_sequence
                    FROM agent_runtime_runs
                    WHERE run_id = $1
                    FOR UPDATE
                    """,
                    run_id,
                )
                if run_row is None:
                    raise KeyError(f"agent run {run_id} not found")

                existing = await connection.fetchrow(
                    f"""
                    SELECT {_EVENT_COLUMNS}
                    FROM agent_runtime_events
                    WHERE run_id = $1 AND event_id = $2
                    """,
                    run_id,
                    draft.event_id,
                )
                if existing is not None:
                    event = _event_from_row(existing)
                    if (
                        event.event_type != draft.event_type
                        or event.payload_sha256 != digest
                        or event.idempotency_key != draft.idempotency_key
                    ):
                        raise RunEventConflict(
                            f"event {draft.event_id} was already appended with "
                            "different immutable content"
                        )
                    return event

                if run_row["status"] != "running":
                    raise RunNotResumable(
                        f"agent run {run_id} is already {run_row['status']}"
                    )

                sequence = int(run_row["next_sequence"])
                event_row = await connection.fetchrow(
                    f"""
                    INSERT INTO agent_runtime_events
                        (run_id, sequence, event_id, event_type, payload,
                         payload_sha256, idempotency_key)
                    VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                    RETURNING {_EVENT_COLUMNS}
                    """,
                    run_id,
                    sequence,
                    draft.event_id,
                    draft.event_type,
                    json.dumps(draft.payload, sort_keys=True),
                    digest,
                    draft.idempotency_key,
                )
                assert event_row is not None

                next_status = _TERMINAL_EVENT_STATUS.get(draft.event_type, "running")
                await connection.execute(
                    """
                    UPDATE agent_runtime_runs
                    SET next_sequence = $2,
                        status = $3,
                        updated_at = now(),
                        completed_at = CASE
                            WHEN $3 = 'running' THEN completed_at ELSE now()
                        END
                    WHERE run_id = $1
                    """,
                    run_id,
                    sequence + 1,
                    next_status,
                )
        return _event_from_row(event_row)

    async def claim_tool_call(
        self,
        run_id: str,
        tool_name: str,
        arguments: dict[str, object],
        *,
        tool_schema_version: str = "v0",
        retry_safe: bool = False,
    ) -> ToolClaim:
        """Decide whether this exact tool call may execute, before it executes.

        中文：在执行前判定该工具调用是否可以运行。

        Called immediately before dispatching a tool. The caller must honour the
        verdict: on "replay" it returns the recorded result without touching the
        tool, and on "needs_review" it stops.

        The interesting branch is the third one. A `tool_started` with no
        terminal event means the process died in the window between claiming the
        call and recording its receipt — the side effect may or may not have
        landed, and the ledger cannot tell which. `retry_safe` is the tool's own
        declaration that running it twice is harmless; without it the call is
        parked instead of guessed at.

        Args:
            run_id: Run this call belongs to.
            tool_name: Registered tool name.
            arguments: The validated arguments, exactly as the tool will get them.
            tool_schema_version: Version of the argument schema.
            retry_safe: Whether re-running the tool after an interrupted attempt
                is harmless. True only for tools whose side effect is idempotent
                on its own.

        Returns:
            A verdict carrying the key to stamp on this call's events.
        """
        key = tool_idempotency_key(
            run_id, tool_name, tool_schema_version, arguments
        )
        rows = await self._pool.fetch(
            """
            SELECT event_type, payload
            FROM agent_runtime_events
            WHERE idempotency_key = $1
            ORDER BY sequence
            """,
            key,
        )
        history = [(str(row["event_type"]), row["payload"]) for row in rows]

        completed = next(
            (payload for event_type, payload in history if event_type == "tool_completed"),
            None,
        )
        if completed is not None:
            return ToolClaim(
                decision="replay",
                idempotency_key=key,
                attempt=sum(1 for event_type, _ in history if event_type == "tool_started"),
                result=_json_object(completed).get("result"),
                reason=f"{tool_name} already completed in this run; returning the recorded result",
            )

        starts = sum(1 for event_type, _ in history if event_type == "tool_started")
        failures = sum(1 for event_type, _ in history if event_type == "tool_failed")
        reviewed = any(event_type == "tool_needs_review" for event_type, _ in history)

        # Every start has a matching terminal event, so nothing is in flight.
        if starts == failures and not reviewed:
            return ToolClaim(
                decision="execute",
                idempotency_key=key,
                attempt=starts + 1,
                reason=(
                    f"no completed record for this call; attempt {starts + 1}"
                    if starts
                    else "first attempt"
                ),
            )

        if reviewed:
            return ToolClaim(
                decision="needs_review",
                idempotency_key=key,
                attempt=starts,
                reason=f"{tool_name} is already parked for review in this run",
            )

        # starts > failures: an attempt was claimed and never resolved.
        if retry_safe:
            return ToolClaim(
                decision="execute",
                idempotency_key=key,
                attempt=starts + 1,
                reason=(
                    f"{tool_name} was interrupted after starting, but declares "
                    f"retry_safe; re-running as attempt {starts + 1}"
                ),
            )
        return ToolClaim(
            decision="needs_review",
            idempotency_key=key,
            attempt=starts,
            reason=(
                f"{tool_name} was interrupted after starting and is not "
                "retry_safe; the side effect may or may not have landed"
            ),
        )

    async def list_events(self, run_id: str) -> list[RunEvent]:
        """Read one run's immutable events in replay order."""

        rows = await self._pool.fetch(
            f"""
            SELECT {_EVENT_COLUMNS}
            FROM agent_runtime_events
            WHERE run_id = $1
            ORDER BY sequence
            """,
            run_id,
        )
        return [_event_from_row(row) for row in rows]

    async def replay(self, run_id: str) -> ReplaySnapshot:
        """Reconstruct and verify a run without calling a model or tool."""

        run = await self.get_run(run_id)
        return replay_events(run, await self.list_events(run_id))

    async def resume_point(self, run_id: str) -> ResumePoint:
        """Return the verified boundary an external runner may continue from."""

        snapshot = await self.replay(run_id)
        if snapshot.run.status != "running":
            raise RunNotResumable(
                f"agent run {run_id} is already {snapshot.run.status}"
            )
        return ResumePoint(
            run_id=run_id,
            next_sequence=snapshot.next_sequence,
            last_event=snapshot.events[-1] if snapshot.events else None,
            pending_tool_calls=snapshot.pending_tool_calls,
        )
