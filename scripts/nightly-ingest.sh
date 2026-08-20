#!/usr/bin/env bash
# Shim. The job lives in the CLI now: `mindbridge ingest` (cli/__main__.py).
# This file stays because the installed LaunchAgent points at this exact path —
# keeping it means the schedule survives without a re-install.
#
# Run by hand:      mindbridge ingest
# Run on schedule:  scripts/mindbridge-scheduler.sh install

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Through the interpreter rather than the console script: launchd starts with a
# bare PATH, and `python -m cli` works whether or not the package is installed.
PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python runtime missing: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m cli ingest "$@"
