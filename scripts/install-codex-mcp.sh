#!/usr/bin/env bash

# Register MindBridge as a local STDIO MCP server in Codex. This script only
# changes Codex's MCP registration; it does not ingest transcripts, write a
# memory, install a scheduler, or alter Docker volumes.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="$REPO_ROOT/scripts/run-mcp.sh"

for command in codex docker ollama; do
  if ! command -v "$command" >/dev/null 2>&1; then
    echo "missing required command: $command" >&2
    exit 1
  fi
done

if [[ ! -f "$REPO_ROOT/.env" ]]; then
  echo "missing .env — copy .env.example to .env first" >&2
  exit 1
fi

if [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
  echo "missing .venv — create it and install requirements.txt first" >&2
  exit 1
fi

if codex mcp get mindbridge >/dev/null 2>&1; then
  echo "MindBridge is already registered. Existing configuration was left unchanged."
else
  codex mcp add mindbridge -- "$LAUNCHER"
  echo "MindBridge registered as a local Codex MCP server."
fi

echo "Start the local data layer: docker compose up -d db redis"
echo "Ensure Ollama has the embedder: ollama pull nomic-embed-text"
echo "Then restart Codex or open a fresh session and run: /mcp"
