#!/usr/bin/env bash
# Shim. The job lives in the CLI now: `mindbridge patterns` (cli/__main__.py).
# This file stays because the installed LaunchAgent points at this exact path.
#
# Run manually for review:  mindbridge patterns --since 30d
# Run on schedule:          scripts/mindbridge-pattern-scheduler.sh run-now
#
# Still write-safe by default: it only prints candidates. MINDBRIDGE_PATTERN_APPLY=1
# and `--apply` are the same switch, and the CLI honours both.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python runtime missing: $PYTHON_BIN" >&2
  exit 1
fi

# The pattern scheduler calls this with a bare window ("30d") while the header
# always documented `--since 30d`. Accept both: a leading dash means the caller
# is speaking the CLI's language already.
if [[ $# -gt 0 && "$1" != -* ]]; then
  since="$1"
  shift
  exec "$PYTHON_BIN" -m cli patterns --since "$since" "$@"
fi

exec "$PYTHON_BIN" -m cli patterns "$@"
