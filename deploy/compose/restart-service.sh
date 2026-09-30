#!/bin/sh
# restart-service.sh — rebuild + restart ONE docker-compose service without disturbing agent-api /
# ollama (the native-hybrid processes dev-up.sh manages). See README.md's "Local hybrid dev" section.
#
# Why this exists: `docker compose up -d <service>` resolves that service's FULL dependency chain
# and starts anything in it that isn't already running — including agent-api, even though it's
# deliberately stopped in native-hybrid mode. `depends_on: ..., required: false` does NOT prevent
# this: it only suppresses an error when a dependency is entirely undefined (e.g. profile-excluded),
# not "currently stopped" — Compose still force-starts an existing, stopped dependency regardless of
# that flag. Reproduced live, repeatedly, in one session: restarting sales-cycle, then terminal, then
# sales-cycle-cron each silently resurrected the docker agent-api container, fighting the native
# process for host port 18100. `--no-deps` is the one flag that actually stops it.
#
# Usage: ./restart-service.sh <service> [<service> ...]   (from deploy/compose/, or anywhere)
set -eu

CD="$(cd "$(dirname "$0")" && pwd)"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <service> [<service> ...]" >&2
  exit 1
fi

echo "== building: $* =="
docker compose -f "$CD/docker-compose.yml" build "$@"

echo "== restarting (--no-deps): $* =="
docker compose -f "$CD/docker-compose.yml" up -d --no-deps "$@"

# Defensive, same as dev-up.sh's own: --no-deps should already make this a no-op, but if a service
# named here genuinely needed agent-api/ollama started fresh (rare — most restarts are of an
# ALREADY-running stack), this puts them back to native-hybrid's intended state rather than leaving
# a docker instance fighting the native one for a port.
docker compose -f "$CD/docker-compose.yml" stop agent-api ollama 2>/dev/null || true

echo
echo "Done. If agent-api or ollama came up anyway, they've been stopped again — the native"
echo "processes (dev-up.sh) remain authoritative."
