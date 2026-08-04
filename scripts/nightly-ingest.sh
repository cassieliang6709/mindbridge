#!/usr/bin/env bash
# Path A, once a night: read new transcript bytes into T1 and rebuild the day
# cards touched. Safe to run at any time and any number of times — turns are
# keyed by source record and cards are rebuilt from the database, so a repeat
# run cannot duplicate a turn or shrink a card.
#
# Run by hand:      scripts/nightly-ingest.sh
# Run on schedule:  scripts/mindbridge-scheduler.sh install

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

LOG_DIR="${MINDBRIDGE_LOG_DIR:-$HOME/Library/Logs/mindbridge}"
LOG_FILE="$LOG_DIR/ingest.log"
mkdir -p "$LOG_DIR"

# Keep the log from growing without bound; one rotation is enough for a job
# that writes a few lines a night.
MAX_LOG_BYTES=$((5 * 1024 * 1024))
if [[ -f "$LOG_FILE" ]]; then
  size=$(wc -c <"$LOG_FILE" | tr -d ' ')
  if ((size > MAX_LOG_BYTES)); then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
  fi
fi

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE"; }

log "--- nightly ingest starting (repo: $REPO_ROOT)"

# Docker Desktop is not running at boot or when the laptop just woke. Fail with
# a readable line rather than a stack trace, and leave the cursors untouched so
# the next run picks up exactly where this one would have.
if ! docker info >/dev/null 2>&1; then
  log "SKIPPED: Docker is not running. Nothing was read; cursors unchanged."
  exit 0
fi

# --wait blocks until the healthchecks pass, so ingest never races the database.
if ! docker compose up -d --wait db redis >>"$LOG_FILE" 2>&1; then
  log "FAILED: could not start db/redis. See lines above."
  exit 1
fi

# --since 3d bounds the file scan by mtime while cursors still decide what is
# actually new, so a machine that was off for a weekend still catches up.
if docker compose run --rm ingest --since 3d >>"$LOG_FILE" 2>&1; then
  log "--- nightly ingest finished"
else
  status=$?
  log "FAILED: ingest exited $status"
  exit "$status"
fi
