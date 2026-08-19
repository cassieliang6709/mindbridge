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

Run a read before asking to write anything:

> Call MindBridge and tell me what writing preferences you remember. Cite the
> memory ids.

For an empty new store, explain that zero results is correct. If the user wants
to test a durable write, ask them for one non-sensitive preference and let the
MCP approval flow confirm `upsert_preference`. Then open another session and
query it again to demonstrate cross-session recall.

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
