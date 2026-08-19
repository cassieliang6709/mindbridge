# Install MindBridge in Codex

These instructions are for a Codex agent helping a user install MindBridge as
a **local STDIO MCP server**. Read the entire guide before changing anything.

## Product and safety boundary

- MindBridge stays local. Do not expose Postgres, Redis, Ollama or the MCP
  process to the public internet.
- Do not ingest transcripts, install the nightly scheduler, or write a test
  memory unless the user separately asks for that action.
- Do not overwrite an existing `.env` or existing `mindbridge` MCP
  registration. Inspect first and stop with a clear explanation on conflict.
- Never print secrets or include `.env` values in chat output.

## Requirements

- macOS or Linux with Git, Python 3.11+, Docker, Ollama and Codex installed.
- Enough local disk for Postgres images and `nomic-embed-text`.

## Install

1. Reuse an existing MindBridge checkout when present. Otherwise ask where the
   user wants it, then clone:

   ```bash
   git clone https://github.com/cassieliang6709/mindbridge.git
   cd mindbridge
   ```

2. Prepare the local runtime without overwriting existing configuration:

   ```bash
   test -f .env || cp .env.example .env
   test -d .venv || python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ollama pull nomic-embed-text
   docker compose up -d db redis
   ```

3. Register the local MCP server:

   ```bash
   chmod +x scripts/run-mcp.sh scripts/install-codex-mcp.sh
   scripts/install-codex-mcp.sh
   codex mcp list
   ```

4. Do not claim the current conversation can see a newly installed MCP server.
   Tell the user to restart Codex or open a fresh session. In that session,
   verify `/mcp` lists `mindbridge`.

## First safe test

Run two separate reads before asking to write anything:

> Call MindBridge's `get_daily_card` for the latest T2 card, then call
> `review_long_term_memory` for the newest three T3 records. Answer under
> separate T2 and T3 headings, cite every id, and do not write.

For an empty new store, explain that zero results is correct. If the user wants
to test a durable write, ask them for one non-sensitive preference and let the
MCP approval flow confirm `upsert_preference`. Then open another session and
query it again to demonstrate cross-session recall.

## Everyday use from Codex

MindBridge currently exposes four tools:

- `get_daily_card` reads one T2 day card: what happened, observed facts and
  open threads. It does not change T3.
- `review_long_term_memory` lists T3 records newest-first for an explicit
  audit. Pass `namespace=operational` or `namespace=reflective` to inspect the
  lanes separately. It does not semantically rank results or bump access counts.
- `temporal_query` recalls relevant T3 memory, newest-weighted, and can search
  operational memory, reflective memory or both.
- `upsert_preference` stores durable operational memory by default. Reflective
  patterns, values and identity hypotheses are rejected unless the user has
  confirmed the wording and the call sets `confirmed_by_user=true`.

Useful requests to give Codex:

### Review what happened today (T2)

> Call `get_daily_card` for the latest T2 card. Cite the card id and summarize
> completed work, observed facts and open threads. Do not infer a personality.

### Audit what lasts across sessions (T3)

> Call `review_long_term_memory` for the newest ten T3 records. Separate current
> from superseded records and cite every memory id.

### Recall before doing work

> Query MindBridge for my writing preferences, cite the memory ids, then edit
> this essay.

### Store a durable preference

> Ask MindBridge to remember: explain technical ideas in plain language and
> start with a concrete example.

Do not store a password, API key, one-time instruction or short-lived task
detail as a preference.

### Supersede an old preference

> My preference changed: write the PRD in English first, then provide a
> separate Chinese version. Ask MindBridge to supersede conflicts.

### Audit history

> Check MindBridge for conflicts in my bilingual-document preferences. Include
> superseded records and cite every memory id.

## Choose a memory operating model

MindBridge does not replace or redirect Codex's native local Memories backend.
Codex-managed files under `~/.codex/memories/` are generated state: do not edit,
replace or symlink them as part of this installation.

### 1. Keep both stores — works today

Leave native Codex Memories enabled and use MindBridge over MCP alongside it.
This requires no migration and preserves native automatic memory injection, but
the two systems may retain overlapping or conflicting facts with different ids
and provenance.

### 2. MindBridge as source of truth — recommended target

Import reviewed native-memory records into MindBridge once, validate dedup and
provenance, then separately disable native memory generation and injection.
Codex, Claude and Cursor can then use the same MindBridge records through MCP.

This repository does not yet ship the native-memory importer. Do not claim the
migration is complete, and do not disable native Memories until the user has a
verified backup and an import report.

### 3. One-way import — safer transition

Periodically import generated Codex memory records into MindBridge without
writing changes back to Codex files. This preserves history and avoids sync
loops. It still leaves two physical stores until native Memories are disabled.

Required rules should remain in `AGENTS.md` or checked-in project documentation
under every model; memory recall is not a substitute for deterministic project
instructions.

## What is running

```text
Codex
  -> local STDIO MCP
MindBridge MemoryService
  -> Postgres / pgvector
  -> Redis exact-query cache
  -> Ollama nomic-embed-text
```

The public MindBridge website uses synthetic data. Installing this local MCP is
what connects Codex to the user's own local memory store.
