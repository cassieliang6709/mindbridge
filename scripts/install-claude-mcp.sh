#!/usr/bin/env bash
# Shim. The work is `mindbridge install claude` (cli/install.py), which is one
# implementation for both clients instead of two files differing in one word.
# This file stays because install.md and the README document this path.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python runtime missing: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m cli install claude
