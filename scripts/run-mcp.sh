#!/usr/bin/env bash
# Shim. `mindbridge mcp` (cli/__main__.py) is the server; after
# `pip install -e .` the console script .venv/bin/mindbridge-mcp is the entry
# point to register with a client. This file stays for clients already pointing
# at it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "MindBridge virtualenv missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e ." >&2
  exit 1
fi

exec .venv/bin/python -m cli mcp
