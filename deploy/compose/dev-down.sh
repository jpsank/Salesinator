#!/bin/sh
# dev-down.sh — stops the two native processes dev-up.sh started (by PID file, never a blind
# `pkill` — a stale/wrong PID file is reported, not force-killed into). Docker services are left
# running by default (fast restart next time); pass --docker too to stop those as well.
set -eu

CD="$(cd "$(dirname "$0")" && pwd)"
PIDDIR="$HOME/vexa-data/pids"

stop_native() {
  name="$1"
  f="$PIDDIR/$name.pid"
  if [ ! -f "$f" ]; then
    echo "  - $name: no pid file, nothing to do"
    return 0
  fi
  pid="$(cat "$f")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "  ✓ stopped $name (pid $pid)"
  else
    echo "  - $name: pid $pid not running"
  fi
  rm -f "$f"
}

echo "== native processes =="
stop_native agent-api
stop_native ollama

if [ "${1:-}" = "--docker" ]; then
  echo "== docker services =="
  docker compose -f "$CD/docker-compose.yml" stop
  # Dangling image layers (orphaned by a rebuild retagging the same name) have no consumer, ever —
  # unlike the runtime's stopped-workload records, there's no grace window to respect, so pruning is
  # safe any time. Only worth paying the cost here, alongside a full teardown, not on every dev-up
  # (which optimizes for fast restart, not disk hygiene). -f, not -a: dangling only, never touches a
  # tagged image still in use.
  echo "== dangling image layers =="
  docker image prune -f
fi

echo
echo "Native processes stopped. Docker services $( [ "${1:-}" = "--docker" ] && echo "stopped too" || echo "left running — pass --docker to stop them" )."
