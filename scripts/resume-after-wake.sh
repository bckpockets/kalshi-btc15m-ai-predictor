#!/bin/sh
# External-wake check. No sudo, no systemd, no cron, no paper start.
# Default: print status and exit. It does not reset a ledger, arm fills, or loop.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$ROOT"

UNTIL=${UNTIL:-}
VENV=${VENV:-"$ROOT/.venv"}
DEPTH_DIR=${DEPTH_DIR:-"$HOME/kalshi-depth-prospective"}
PAPER_DIR=${PAPER_DIR:-}
START_DEPTH=0
RESTORE_VENV=0
CHECK_LOCK=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --restore-venv) RESTORE_VENV=1 ;;
    --check-lock) CHECK_LOCK=1 ;;
    --start-depth) START_DEPTH=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --until) UNTIL=${2:?}; shift ;;
    --depth-dir) DEPTH_DIR=${2:?}; shift ;;
    --paper-dir) PAPER_DIR=${2:?}; shift ;;
    --help)
      echo "Usage: $0 [--paper-dir DIR] [--check-lock] [--restore-venv] [--start-depth --until UTC --depth-dir DIR] [--dry-run]"
      echo "Interpreter: \$VENV/bin/python when that file is executable, otherwise python3."
      echo "Depth restart module: python -m research.runner (there is no research.supervise)."
      echo "Does not delete data, reset a bankroll, arm fills, or start the paper engine."
      echo "Paper kickoff (run_taker.py) refuses an existing database. Resume check: python -m research.resume --sqlite LEDGER"
      echo "Attaching that process is a separate explicit command and is not invoked here."
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

if [ -x "$VENV/bin/python" ]; then
  PY="$VENV/bin/python"
else
  PY=python3
fi

echo "recovery-check utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "repo=$ROOT"
echo "interpreter=$PY"
echo "interpreter_version=$("$PY" -V 2>&1)"
echo "requirements_lock=requirements.lock (hashed; compiled for Python 3.11; this does not recreate the missing interpreter)"

PREFLIGHT=0
if [ -n "$PAPER_DIR" ]; then
  echo "paper_dir=$PAPER_DIR"
  SQLITE="$PAPER_DIR/runtime.sqlite"
  if [ -f "$SQLITE" ]; then
    echo "ledger=present (opened read-only by research.resume; not reset)"
    if "$PY" -m research.resume --sqlite "$SQLITE" --status "$PAPER_DIR/status.json"; then
      echo "preflight=pass"
    else
      echo "preflight=fail"
      PREFLIGHT=2
    fi
  else
    echo "ledger=absent"
    PREFLIGHT=2
  fi
else
  echo "paper_dir=unset"
fi
echo "paper_start=not performed by this script"

if [ -d "$DEPTH_DIR" ] && [ -f "$DEPTH_DIR/state.json" ]; then
  echo "depth_state=$DEPTH_DIR/state.json"
  "$PY" - "$DEPTH_DIR/state.json" <<'PY'
import json, sys
state=json.load(open(sys.argv[1]))
print("depth_last_ticker=%s" % state.get("last_ticker"))
print("depth_local_seq=%s" % state.get("local_seq"))
print("depth_last_received_at=%s" % state.get("last_received_at"))
print("depth_note=resume appends; a gap uses the last parsed segment receive time; missing polls are not invented")
PY
else
  echo "depth_state=absent"
fi

if [ "$CHECK_LOCK" -eq 1 ]; then
  "$PY" -m pip install --require-hashes --dry-run -r "$ROOT/requirements.lock"
  echo "lock_check=dry-run"
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "venv=missing path=$VENV"
  if [ "$RESTORE_VENV" -eq 1 ]; then
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --require-hashes -r "$ROOT/requirements.lock"
    echo "venv=restored-from-requirements.lock"
    PY="$VENV/bin/python"
    echo "interpreter=$PY"
  else
    echo "venv_action=not restored (pass --restore-venv to install requirements.lock with --require-hashes; there is no unpinned fallback)"
  fi
else
  echo "venv=present path=$VENV"
fi

if [ "$PREFLIGHT" -ne 0 ]; then
  echo "stopping before depth start because the paper preflight failed" >&2
  exit "$PREFLIGHT"
fi

if [ "$START_DEPTH" -eq 0 ]; then
  echo "depth_start=not requested"
  exit 0
fi

if [ -z "$UNTIL" ]; then
  echo "--start-depth requires --until UTC" >&2
  exit 64
fi
if [ ! -x "$VENV/bin/python" ]; then
  echo "cannot start depth without a venv" >&2
  exit 3
fi

# research.runner is the module. research.supervise does not exist.
set -- "$PY" -m research.runner --until "$UNTIL" -- \
  "$PY" -m research.depth_recorder --output "$DEPTH_DIR" --until "$UNTIL" --poll-seconds 3
echo "depth_command=$*"
if [ "$DRY_RUN" -eq 1 ]; then
  echo "depth_start=dry-run"
  exit 0
fi
exec "$@"
