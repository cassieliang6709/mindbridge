#!/usr/bin/env bash
# Detect recurring T2 signals and generate Pattern Candidates from local cards.
#
# Run manually for review:
#   scripts/nightly-patterns.sh --since 30d
#
# Run on schedule (default dry-run):
#   scripts/mindbridge-pattern-scheduler.sh run-now
#
# This script is write-safe by default: it only prints candidates.
# Enable one-time writes with MINDBRIDGE_PATTERN_APPLY=1.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="${MINDBRIDGE_LOG_DIR:-$HOME/Library/Logs/mindbridge}"
LOG_FILE="$LOG_DIR/pattern-discovery.log"
mkdir -p "$LOG_DIR"

MAX_LOG_BYTES=$((5 * 1024 * 1024))
if [[ -f "$LOG_FILE" ]]; then
  size=$(wc -c <"$LOG_FILE" | tr -d ' ')
  if (( size > MAX_LOG_BYTES )); then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
  fi
fi

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE"; }

PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  log "FAILED: python runtime not found at ${PYTHON_BIN}."
  echo "python runtime missing: ${PYTHON_BIN}" >&2
  exit 1
fi

since="${1:-30d}"
if docker info >/dev/null 2>&1; then
  if ! docker compose up -d --wait db redis >>"$LOG_FILE" 2>&1; then
    log "FAILED: could not start db/redis for pattern discovery."
    echo "Could not start db/redis; see $LOG_FILE" >&2
    exit 1
  fi
else
  log "SKIPPED: Docker is not running. Pattern discovery needs local data layer."
  echo "Docker not running; skipping pattern discovery." >&2
  exit 0
fi

cmd=(
  "$PYTHON_BIN" -m scripts.suggest_patterns
  "--since" "$since"
  "--card-limit" "${MINDBRIDGE_PATTERN_SCAN_LIMIT:-365}"
  "--max-supporting" "${MINDBRIDGE_PATTERN_SUPPORTING:-10}"
  "--max-counter-evidence" "0"
  "--limit" "${MINDBRIDGE_PATTERN_DAILY_LIMIT:-40}"
)

if [[ "${MINDBRIDGE_PATTERN_APPLY:-0}" == "1" ]]; then
  cmd+=(--apply)
  log "pattern discovery: running in APPLY mode"
fi

log "pattern discovery starting (${cmd[*]})"
if "${cmd[@]}" >>"$LOG_FILE" 2>&1; then
  log "--- pattern discovery finished"
else
  status=$?
  log "FAILED: pattern discovery exited $status"
  exit "$status"
fi

exit 0
