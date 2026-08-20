#!/usr/bin/env bash
# Shim. The work is `mindbridge schedule ingest {status,run-now,install,uninstall}`
# (cli/schedule.py). Arguments are forwarded verbatim, so `install` now prints
# the plist it would write and stops until you pass --confirm.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python runtime missing: $PYTHON_BIN" >&2
  exit 1
fi

exec "$PYTHON_BIN" -m cli schedule ingest "$@"
