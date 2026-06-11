#!/bin/bash
# check_plan_busy.sh — is there an active wake-up session for this plan?
#
# Reads orchestrator/<plan_id>/.wake_lock, parses the owner PID, and tests
# whether that process is still alive via `kill -0`. Prints one of:
#
#   idle (no lock file)
#   idle (lock file empty)
#   idle (stale lock — holder pid=N is no longer alive)
#   BUSY: wake-up active (pid=N, cmd='...')
#
# Exit codes:
#   0 — plan is NOT busy (safe to proceed with an interactive session)
#   1 — plan IS busy (another wake-up is currently executing this plan)
#   2 — usage / internal error
#
# Used at interactive orchestrator session-start (SKILL.md §3, "Continuing an
# existing task") to prevent a user-driven session from racing a scheduled
# wake-up. The flock in wake_up.sh already protects wake-up-vs-wake-up; this
# check protects wake-up-vs-interactive.
#
# Invocation:
#   bash orchestrator/check_plan_busy.sh <plan_id>

set -uo pipefail

if [ $# -ne 1 ]; then
    echo "usage: $0 <plan_id>" >&2
    exit 2
fi

PLAN_ID="$1"
PROJECT_DIR="/home/sharf-lab/Desktop/Research_automation"
LOCK="$PROJECT_DIR/orchestrator/$PLAN_ID/.wake_lock"

if [ ! -f "$LOCK" ]; then
    echo "idle (no lock file)"
    exit 0
fi

PID="$(tr -d '[:space:]' < "$LOCK" 2>/dev/null)"
if [ -z "$PID" ]; then
    echo "idle (lock file empty)"
    exit 0
fi

if ! echo "$PID" | grep -qE '^[0-9]+$'; then
    echo "idle (lock file contains non-numeric data: '$PID')"
    exit 0
fi

if kill -0 "$PID" 2>/dev/null; then
    CMDLINE="$(tr '\0' ' ' < /proc/$PID/cmdline 2>/dev/null | sed 's/ $//')"
    [ -z "$CMDLINE" ] && CMDLINE="<unreadable>"
    echo "BUSY: wake-up active (pid=$PID, cmd='$CMDLINE')"
    exit 1
fi

echo "idle (stale lock — holder pid=$PID is no longer alive)"
exit 0
