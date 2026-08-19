#!/usr/bin/env bash

# Register MindBridge as a user-scoped local STDIO MCP server in Claude Code.
# This changes only Claude Code's MCP registration. Transcript ingestion is a
# separate, explicit Path A command and remains read-only at the source.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="$REPO_ROOT/scripts/run-mcp.sh"

for command in claude docker ollama; do
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

if claude mcp get mindbridge >/dev/null 2>&1; then
  echo "MindBridge is already registered in Claude Code. Existing configuration was left unchanged."
else
  claude mcp add --scope user mindbridge -- "$LAUNCHER"
  echo "MindBridge registered as a user-scoped Claude Code MCP server."
fi

echo "Start the local data layer: docker compose up -d db redis"
echo "Ensure Ollama has the embedder: ollama pull nomic-embed-text"
echo "Then restart Claude Code or open a fresh session and run: /mcp"
