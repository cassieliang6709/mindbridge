#!/usr/bin/env bash
# Manage the nightly Pattern Candidate scan as a macOS launchd agent.
#
#   scripts/mindbridge-pattern-scheduler.sh status
#   scripts/mindbridge-pattern-scheduler.sh run-now
#   scripts/mindbridge-pattern-scheduler.sh install
#   scripts/mindbridge-pattern-scheduler.sh uninstall
#
# `install` is opt-in and writes a LaunchAgent under ~/Library/LaunchAgents.
# Default schedule is 00:45 local; override with env vars:
#   MINDBRIDGE_PATTERN_HOUR, MINDBRIDGE_PATTERN_MINUTE, MINDBRIDGE_PATTERN_APPLY

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.mindbridge.nightly-patterns"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="${MINDBRIDGE_LOG_DIR:-$HOME/Library/Logs/mindbridge}"
HOUR="${MINDBRIDGE_PATTERN_HOUR:-0}"
MINUTE="${MINDBRIDGE_PATTERN_MINUTE:-45}"
PATH_VALUE="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
PATTERN_APPLY="${MINDBRIDGE_PATTERN_APPLY:-0}"
SINCE="${MINDBRIDGE_PATTERN_SINCE:-30d}"
SCAN_LIMIT="${MINDBRIDGE_PATTERN_SCAN_LIMIT:-365}"
SUPPORTING="${MINDBRIDGE_PATTERN_SUPPORTING:-10}"
DAILY_LIMIT="${MINDBRIDGE_PATTERN_DAILY_LIMIT:-40}"
PYTHON_BIN="${MINDBRIDGE_PYTHON_BIN:-$REPO_ROOT/.venv/bin/python}"

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
    <string>$REPO_ROOT/scripts/nightly-patterns.sh</string>
    <string>$SINCE</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
    <key>MINDBRIDGE_PATTERN_APPLY</key>
    <string>$PATTERN_APPLY</string>
    <key>MINDBRIDGE_PATTERN_SCAN_LIMIT</key>
    <string>$SCAN_LIMIT</string>
    <key>MINDBRIDGE_PATTERN_SUPPORTING</key>
    <string>$SUPPORTING</string>
    <key>MINDBRIDGE_PATTERN_DAILY_LIMIT</key>
    <string>$DAILY_LIMIT</string>
    <key>MINDBRIDGE_PATTERN_SINCE</key>
    <string>$SINCE</string>
    <key>MINDBRIDGE_PYTHON_BIN</key>
    <string>$PYTHON_BIN</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/pattern-discovery.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/pattern-discovery.err.log</string>
</dict>
</plist>
PLIST_EOF
}

case "${1:-status}" in
  install)
    write_plist
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST"
    echo "installed: $LABEL runs daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
    echo "plist:     $PLIST"
    echo "log:      $LOG_DIR/pattern-discovery.log"
    echo
    echo "Dry-run mode by default. Set MINDBRIDGE_PATTERN_APPLY=1 before"
    echo "reinstall to persist writes automatically."
    ;;
  uninstall)
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed: $LABEL (logs kept in $LOG_DIR)"
    ;;
  run-now)
    exec "$REPO_ROOT/scripts/nightly-patterns.sh" "$SINCE"
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
    if [[ -f "$LOG_DIR/pattern-discovery.log" ]]; then
      echo "last log lines:"
      tail -n 8 "$LOG_DIR/pattern-discovery.log" | sed 's/^/  /'
    else
      echo "log:       none yet ($LOG_DIR/pattern-discovery.log)"
    fi
    ;;
  *)
    echo "usage: $0 {status|run-now|install|uninstall}" >&2
    exit 2
    ;;
esac
