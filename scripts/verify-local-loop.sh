#!/usr/bin/env bash
# Shim. The work is `mindbridge verify` (cli/verify.py). Add --plan to see which
# services it would start without starting any of them.
#
# This file stays because the README documents this path as the one-command
# interview proof.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python runtime missing: $PYTHON_BIN" >&2
  exit 1
fi

# The date was always a bare positional here; the CLI spells it --date. A
# leading dash means the caller is speaking the CLI's language already.
if [[ $# -gt 0 && "$1" != -* ]]; then
  date="$1"
  shift
  exec "$PYTHON_BIN" -m cli verify --date "$date" "$@"
fi

exec "$PYTHON_BIN" -m cli verify "$@"
