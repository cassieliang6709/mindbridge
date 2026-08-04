#!/usr/bin/env bash
# Manage the nightly Path A job as a macOS launchd agent.
#
#   scripts/mindbridge-scheduler.sh status      # is it installed / when did it last run
#   scripts/mindbridge-scheduler.sh run-now     # run once in the foreground
#   scripts/mindbridge-scheduler.sh install     # schedule it (writes a LaunchAgent)
#   scripts/mindbridge-scheduler.sh uninstall   # remove the schedule
#
# `install` writes a file to ~/Library/LaunchAgents and registers it with
# launchd — a persistent change to your machine, which is why it is opt-in and
# never runs as part of a build or a test. `status` and `run-now` change
# nothing.
#
# launchd wakes the job at the scheduled hour; if the Mac was asleep it runs
# once shortly after wake, and the script exits quietly when Docker is down.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.mindbridge.nightly-ingest"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="${MINDBRIDGE_LOG_DIR:-$HOME/Library/Logs/mindbridge}"
HOUR="${MINDBRIDGE_INGEST_HOUR:-23}"
MINUTE="${MINDBRIDGE_INGEST_MINUTE:-30}"

write_plist() {
  mkdir -p "$(dirname "$PLIST")" "$LOG_DIR"
  cat >"$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$REPO_ROOT/scripts/nightly-ingest.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <!-- Docker lives in /usr/local/bin or /opt/homebrew/bin; launchd starts with
       a minimal PATH, so the job would not find it otherwise. -->
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.err.log</string>
</dict>
</plist>
PLIST_EOF
}

case "${1:-status}" in
  install)
    write_plist
    # bootout first so re-installing picks up a changed schedule.
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST"
    echo "installed: $LABEL runs daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
    echo "plist:     $PLIST"
    echo "logs:      $LOG_DIR/ingest.log"
    echo
    echo "Docker Desktop must be running at that hour, or the job logs a skip"
    echo "and leaves the cursors alone."
    ;;
  uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed: $LABEL (logs kept in $LOG_DIR)"
    ;;
  run-now)
    exec "$REPO_ROOT/scripts/nightly-ingest.sh"
    ;;
  status)
    if [[ -f "$PLIST" ]]; then
      echo "plist:     present ($PLIST)"
    else
      echo "plist:     not installed"
    fi
    if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
      echo "launchd:   loaded, daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
    else
      echo "launchd:   not loaded"
    fi
    if [[ -f "$LOG_DIR/ingest.log" ]]; then
      echo "last log lines:"
      tail -n 5 "$LOG_DIR/ingest.log" | sed 's/^/  /'
    else
      echo "log:       none yet ($LOG_DIR/ingest.log)"
    fi
    ;;
  *)
    echo "usage: $0 {status|run-now|install|uninstall}" >&2
    exit 2
    ;;
esac
