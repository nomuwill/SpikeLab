#!/bin/bash
# schedule_wakeup.sh — schedule a primary/backup/retry wake-up for an
# orchestrator plan, with RuntimeMaxSec watchdog property + pending_wakeups.json
# bookkeeping. This is the *only* sanctioned way to schedule a wake-up —
# ad-hoc systemd-run calls risk forgetting --property=RuntimeMaxSec, which
# would let a wedged session hold the plan's .wake_lock indefinitely.
#
# Invocation:
#   bash orchestrator/schedule_wakeup.sh <plan_id> <role> <run_number_padded> \
#        <target_step> <fire_time_utc> [reason]
#
#   plan_id           e.g. maturity-pilot_2026-04-14
#   role              primary|backup|retry
#   run_number_padded e.g. 003   (zero-padded to 3 digits)
#   target_step       short descriptor, e.g. run3_mc3
#   fire_time_utc     ISO-8601 UTC, e.g. 2026-04-16T12:00:00Z
#   reason            optional free-text reason for pending_wakeups.json
#
# Reads `wake_up_max_runtime` from the plan's experiment_plan.md §11 and
# passes it as --property=RuntimeMaxSec=<N>. Default if unset: 6h.
#
# Appends an entry to pending_wakeups.json (does not use schedule_util because
# pending_wakeups.json is a separate file with its own lock semantics — see
# SKILL.md `pending_wakeups.json` section).

set -uo pipefail

if [ $# -lt 5 ]; then
    echo "usage: $0 <plan_id> <role> <run_number_padded> <target_step> <fire_time_utc> [reason]" >&2
    exit 2
fi

PLAN_ID="$1"
ROLE="$2"
RUN_NUMBER="$3"
TARGET_STEP="$4"
FIRE_TIME_UTC="$5"
REASON="${6:-scheduled via schedule_wakeup.sh}"

PROJECT_DIR="/home/sharf-lab/Desktop/Research_automation"
PLAN_DIR="$PROJECT_DIR/orchestrator/$PLAN_ID"
PLAN_MD="$PLAN_DIR/experiment_plan.md"
WAKE_SH="$PROJECT_DIR/orchestrator/wake_up.sh"

if [ ! -d "$PLAN_DIR" ]; then
    echo "FATAL: plan dir does not exist: $PLAN_DIR" >&2
    exit 3
fi
if [ ! -f "$PLAN_MD" ]; then
    echo "FATAL: plan file does not exist: $PLAN_MD" >&2
    exit 3
fi
case "$ROLE" in
    primary|backup|retry) ;;
    *) echo "FATAL: role must be primary|backup|retry, got '$ROLE'" >&2; exit 2 ;;
esac

# Extract wake_up_max_runtime from the plan. Matches lines like:
#   - **`wake_up_max_runtime`:** 6h
#   - wake_up_max_runtime: 12h
# Falls back to 6h if nothing matches.
WAKE_MAX="$(
    grep -iE 'wake_up_max_runtime' "$PLAN_MD" 2>/dev/null \
    | head -1 \
    | grep -oiE '([0-9]+)(h|hr|hrs|min|mins|s|sec|d|day|days)\b' \
    | head -1
)"
if [ -z "$WAKE_MAX" ]; then
    WAKE_MAX="6h"
fi

# Normalize to systemd.time(7) duration (6h, 30min, 10s, 2d — already fine if
# matched above).

# Compute timing fields.
FIRE_LOCAL="$(date -d "$FIRE_TIME_UTC" +'%Y-%m-%d %H:%M:%S' 2>/dev/null)"
if [ -z "$FIRE_LOCAL" ]; then
    echo "FATAL: could not parse fire_time_utc: $FIRE_TIME_UTC" >&2
    exit 2
fi
FIRE_TSTAG="$(date -u -d "$FIRE_TIME_UTC" +'%Y-%m-%dT%H%M%SZ')"
SCHED_UTC="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

UNIT="orchestrator-${PLAN_ID}-run${RUN_NUMBER}-${ROLE}"
LOG="$PLAN_DIR/logs/wakeups/${ROLE}_${FIRE_TSTAG}.log"

mkdir -p "$(dirname "$LOG")"

# Launch the transient systemd timer. RuntimeMaxSec is the watchdog — systemd
# kills the whole cgroup (claude + subagents + external subprocesses) at this
# ceiling.
systemd-run --user \
    --on-calendar="$FIRE_LOCAL" \
    --unit="$UNIT" \
    --property=RuntimeMaxSec="$WAKE_MAX" \
    /bin/bash "$WAKE_SH" "$PLAN_ID" "$ROLE" "$TARGET_STEP" "$LOG"

RC=$?
if [ $RC -ne 0 ]; then
    echo "FATAL: systemd-run failed (rc=$RC)" >&2
    exit $RC
fi

# Append to pending_wakeups.json atomically. Uses a small inline Python for
# JSON handling.
PYTHON_BIN="${PYTHON:-/home/sharf-lab/miniconda3/envs/automation/bin/python}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"

# Prevent .pyc clutter — this script is invoked frequently and the inline
# Python below imports nothing user-defined, so bytecode caching has no
# benefit and just creates orchestrator/__pycache__/.
PYTHONDONTWRITEBYTECODE=1 "$PYTHON_BIN" - "$PLAN_DIR" "$UNIT" "$ROLE" "$TARGET_STEP" "$FIRE_TIME_UTC" "$SCHED_UTC" "$LOG" "$WAKE_MAX" "$REASON" <<'PYEOF'
import json, os, sys, tempfile
from pathlib import Path

plan_dir, unit, role, step, fire_utc, sched_utc, log_path, wake_max, reason = sys.argv[1:]
pending = Path(plan_dir) / "pending_wakeups.json"

if pending.exists():
    with open(pending) as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = []
else:
    data = []

data.append({
    "unit": unit,
    "role": role,
    "target_step": step,
    "fire_time": fire_utc,
    "scheduled_at": sched_utc,
    "log_path": log_path,
    "runtime_max": wake_max,
    "reason": reason,
})

tmp_fd, tmp_name = tempfile.mkstemp(prefix=".pending_", suffix=".tmp", dir=str(pending.parent))
with os.fdopen(tmp_fd, "w") as f:
    json.dump(data, f, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp_name, pending)
print(f"pending_wakeups.json now has {len(data)} entries")
PYEOF

echo "scheduled $UNIT"
echo "  fire time: $FIRE_LOCAL local ($FIRE_TIME_UTC)"
echo "  RuntimeMaxSec: $WAKE_MAX"
echo "  log: $LOG"
