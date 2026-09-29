# MindBridge Agent Runtime v0

Status: ledger, versioned read-tool registry, local Qwen tool-selection loop,
and minimal Web UI implemented. Crash-safe writes are not yet wired.

## Product slice

One future MindBridge agent run should be able to recall a past project decision,
show its source, request a confirmed memory update, and leave enough durable
state to inspect, resume, or replay the run.

`MemoryAgentRuntime` records and executes `temporal_query` and
`upsert_preference`. `LocalMemoryAgent` now gives the local `qwen2.5:7b` model
the JSON Schema generated from `TemporalQueryRequest`, validates the returned
arguments with that same model, executes at most one read tool, and calls the
model once more for a grounded answer.

## Existing boundary and gap map

| Concern | Existing code | v0 status |
|---|---|---|
| Long-term memory | T1/T2/T3 stores behind `MemoryService` | existing |
| Tool discovery | FastMCP derives JSON schemas from decorated functions | existing |
| Cross-transport behavior | REST and MCP share `MemoryService` | existing |
| Transcript resume | per-file byte cursors and `source_key` | existing, ingestion only |
| Extraction replay | captured JSONL can rebuild derived memories | existing, extraction only |
| Agent run identity | no first-class run record | `agent_runtime_runs` |
| Ordered agent events | external harness behavior was implicit | `agent_runtime_events` |
| Deterministic replay | no agent-level integrity check | `AgentRunLedger.replay()` |
| Resume boundary | no durable next step or pending tool set | `resume_point()` |
| Exactly-once tool effects | semantic dedup is not an execution receipt | not implemented |
| Approval/capability policy | encoded only in MCP instructions | not implemented |
| Memory tool lifecycle | previously owned by Codex/Claude | `MemoryAgentRuntime` |
| Model loop | previously owned by Codex/Claude | local Qwen, one read tool max |

## Event contract

Every event has a caller-supplied `event_id`, a sequence allocated while the run
row is locked, an event type, JSON payload, and SHA-256 over canonical
`{event_type, payload}`. Re-appending the same event is allowed only when its
immutable content matches exactly.

Tool lifecycle events carry `payload.tool_call_id`:

```text
tool_requested -> tool_approved -> tool_started -> tool_completed
                                                -> tool_failed
```

Replay rejects gaps, reordered events, changed payloads, events from another
run, invalid tool-state transitions, and terminal-status mismatches.
`run_completed` or `run_failed` must be the last event.

## Invariants

1. `(run_id, sequence)` and `(run_id, event_id)` are unique.
2. Sequence allocation and run advancement occur in one database transaction.
3. A terminal run accepts no new event, except an identical retry of an event
   already stored.
4. Replay calls no model and executes no tool.
5. A missing or changed event fails loudly instead of producing a partial view.

## Privacy and training boundary

Runtime events can contain retrieved memory, write arguments, and tool results.
They remain in the same local Postgres boundary as the memory service and are
**not dataset-eligible by default**. A future learning-data exporter must apply
redaction, replace governed payloads with hashes/artifact references, and record
the policy version that approved promotion. Run tracing is not consent to train.

## Honest crash boundary

The ledger makes event append idempotent; it does **not** make arbitrary tool
side effects exactly once. There is still a crash window:

```text
tool changes external state -> process dies -> tool_completed was never appended
```

Closing that window requires one of:

- the tool accepts `event_id`/`tool_call_id` as an idempotency key and returns
  the stored result when retried; or
- the tool effect and its completion receipt commit in the same transaction.

`MemoryAgentRuntime` therefore retries a pending read but blocks a pending write
with `UncertainToolOutcome`. The final write path should use the second option
because memory and ledger share PostgreSQL. Until that is implemented and
tested, do not claim exactly-once memory writes or automatic crash recovery.

## Acceptance checks for this slice

- identical event retries produce one stored event;
- identical run creation retries return the existing run, while changed
  configuration under the same run id is rejected;
- reusing an event id with different content is rejected;
- concurrent append positions are allocated under a locked run row;
- replay detects sequence gaps and payload tampering;
- resume identifies requested/started tool calls without a terminal event;
- terminal runs reject new work;
- existing MindBridge tests remain green.

## Local Web UI

The local agent is available at `http://127.0.0.1:3000/runtime` when the FastAPI
service and Next.js dev server are running. The default surface is deliberately
one question box and one answer; the trace is collapsed under **Developer
details**. It exercises the product path rather than a browser-only simulation:

1. The model receives only the versioned `temporal_query` definition.
2. Its selected tool and arguments are recorded before execution.
3. The real `MemoryService.temporal_query` call is bracketed by durable
   `tool_requested`, `tool_started`, and `tool_completed` events.
4. The model receives the tool result and produces the visible answer.
5. `run_completed` stores the answer and receipt; the browser remembers the
   last Run ID and can restore it after refresh.

Start it locally:

```bash
.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
npm run dev -- --hostname 127.0.0.1 --port 3000
```

The registry currently exposes only one read tool and the loop executes at most
one tool call. No write or external-side-effect tool is model-visible.

## Retrieval quality is a separate open problem

The live resume-expression query completed correctly as a harness run, but its
five retrieved memories were broad and only weakly related to the question.
That is evidence that tool orchestration works and retrieval quality does not
yet meet the product bar. Do not use a successful trace as a retrieval metric.
The next retrieval change should start with a fixed, source-labelled query set
and compare the current vector ranking against a hybrid or reranked candidate
set before changing production behavior.

That baseline now lives in `evals/retrieval_quality.py` and
`evals/retrieval_quality_cases.json`. It runs the production
pgvector/time-decay ranking while bypassing caches and `access_count` writes.
Cases captured from real agent runs begin as `draft`. Confirmation records both
the complete candidate pool shown to the reviewer and the IDs marked relevant.
Only fully judged result sets contribute to Precision@K or MRR; a new ranker
that returns unseen IDs produces null metrics until those candidates are
reviewed. Recall is intentionally not reported because selecting positives from
the current Top-K does not establish the relevant set across the whole corpus.

```bash
python -m evals.retrieval_quality
```

The generated `evals/retrieval_quality_latest.json` records the dataset hash,
embedder, corpus size, ranking parameters, per-hit scores, and source run ID.
No hybrid or reranked path should replace production retrieval until confirmed
labels show a repeatable improvement over this baseline.

## Next slice

Evaluate and repair retrieval on a source-labelled query set. In parallel, give
the memory write a tool-call receipt committed atomically with the T3 mutation,
then test a forced crash after the mutation but before the next model step.
Only after that invariant is real should the registry expose a write tool.
