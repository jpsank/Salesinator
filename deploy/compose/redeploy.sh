#!/bin/sh
# redeploy.sh — rebuild EVERYTHING that can change, restart only what actually changed, and
# recycle any running chat worker left on a now-stale image. The "just works" button: no more
# figuring out which named service your edit landed in and running restart-service.sh on it by hand.
#
# What it does, per run:
#   1. Every docker-compose service with its own `build:` block (except agent-api/agent-worker,
#      handled specially below) gets rebuilt — cache-cheap when nothing changed, a no-op run just
#      finishes fast. Each one's image ID is compared before/after; only a service whose image
#      actually changed gets restarted (`--no-deps`, same as restart-service.sh, so the native-hybrid
#      stand-ins for agent-api/ollama are never disturbed).
#   2. agent-worker is special: it's never `up -d`'d as a long-running container (profile:
#      build-only — runtime-api spawns it per chat turn). Docker never swaps a RUNNING container
#      onto a rebuilt image — nothing does that anywhere, this isn't a gap specific to us — so after
#      rebuilding it, every CURRENTLY RUNNING vexa-worker-* container still on the OLD image gets
#      torn down through runtime-api's own DELETE /workloads/{id}. Never raw `docker rm` — that
#      desyncs runtime-api's own bookkeeping from reality (cost a whole debugging session once
#      already: a "destroyed" container whose workload record still said "running" forever
#      afterward, so every future touch silently no-opped instead of spawning fresh). The next
#      message to that chat thread then spawns genuinely fresh on the new image.
#   3. agent-api is special too: in native-hybrid mode its docker image is never actually run
#      (deliberately kept stopped — see run-agent-api-native.sh), so building/restarting the DOCKER
#      side of it would be pure waste. Instead the NATIVE process is unconditionally killed and
#      relaunched (cheap — a few seconds) so it always runs current source, no change-detection
#      needed.
#
# Usage: ./redeploy.sh   (from deploy/compose/, or anywhere)
set -eu

CD="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE="$CD/docker-compose.yml"
RUNTIME_API_URL="${RUNTIME_API_URL:-http://localhost:18090}"

# Every buildable, normally-running compose service EXCEPT agent-api/agent-worker (handled above).
# flows-mailbox is DELIBERATELY excluded: it shares flows-api's image (so building flows-api already
# covers it) but isn't in `docker compose config --services` and has no mail credentials configured
# in this deployment — it was never meant to auto-start. Reproduced live: an earlier version of this
# script that included it in the restart loop spawned it for the first time ever and it crash-looped
# on a ConfigError, because nothing actually wants it running here.
SERVICES="admin-api runtime sales-cycle meeting-api gateway flows-api flows-worker mcp terminal"

echo "== building: $SERVICES agent-worker =="
# shellcheck disable=SC2086
docker compose -f "$COMPOSE_FILE" build $SERVICES agent-worker

echo
echo "== restarting only what actually changed =="
for svc in $SERVICES; do
  img="$(docker compose -f "$COMPOSE_FILE" config --images "$svc" 2>/dev/null | head -1)"
  if [ -z "$img" ]; then
    echo "  $svc: no image configured — skipping"
    continue
  fi
  built_id="$(docker image inspect "$img" --format '{{.Id}}' 2>/dev/null || true)"
  cid="$(docker compose -f "$COMPOSE_FILE" ps -q "$svc" 2>/dev/null || true)"
  running_id=""
  [ -n "$cid" ] && running_id="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null || true)"
  if [ -z "$running_id" ] || [ "$built_id" != "$running_id" ]; then
    echo "  $svc: image changed (or not running) — restarting"
    docker compose -f "$COMPOSE_FILE" up -d --no-deps "$svc"
  else
    echo "  $svc: unchanged — left running"
  fi
done
# Same gotcha restart-service.sh exists for: `up -d` resolves each service's full dependency chain
# and force-starts anything in it that's stopped, including agent-api — even with --no-deps on
# EVERY individual call above, a service still naming agent-api as a dependency can resurrect it.
docker compose -f "$COMPOSE_FILE" stop agent-api ollama 2>/dev/null || true

echo
echo "== recycling any running chat worker still on the old agent-worker image =="
worker_img="$(docker compose -f "$COMPOSE_FILE" config --images agent-worker 2>/dev/null | head -1)"
worker_built_id="$(docker image inspect "$worker_img" --format '{{.Id}}' 2>/dev/null || true)"
if [ -n "$worker_built_id" ]; then
  docker ps --filter "name=vexa-worker-" --format "{{.Names}}" | while read -r name; do
    [ -z "$name" ] && continue
    running_id="$(docker inspect "$name" --format '{{.Image}}' 2>/dev/null || true)"
    if [ "$running_id" != "$worker_built_id" ]; then
      workload_id="agent-${name#vexa-worker-}"
      echo "  $name: stale — destroying workload $workload_id via runtime-api"
      curl -s -X DELETE "$RUNTIME_API_URL/workloads/$workload_id" -o /dev/null -w "    -> http %{http_code}\n" || true
    fi
  done
else
  echo "  (no agent-worker image built — skipping)"
fi

echo
echo "== restarting native agent-api (always — cheap, and it's never 'just a container') =="
PIDDIR="$HOME/vexa-data/pids"
LOGDIR="$HOME/vexa-data/logs"
mkdir -p "$PIDDIR" "$LOGDIR"
if [ -f "$PIDDIR/agent-api.pid" ]; then
  old="$(cat "$PIDDIR/agent-api.pid")"
  kill "$old" 2>/dev/null || true
  i=0
  while [ "$i" -lt 10 ] && kill -0 "$old" 2>/dev/null; do
    sleep 0.5
    i=$((i + 1))
  done
  kill -9 "$old" 2>/dev/null || true
fi
nohup "$CD/run-agent-api-native.sh" >"$LOGDIR/agent-api.log" 2>&1 &
echo $! >"$PIDDIR/agent-api.pid"
i=0
while [ "$i" -lt 10 ]; do
  curl -s -o /dev/null --max-time 1 http://localhost:18100/health && break
  sleep 1
  i=$((i + 1))
done
curl -s -o /dev/null -w "  health http=%{http_code}\n" http://localhost:18100/health
docker compose -f "$COMPOSE_FILE" stop agent-api ollama 2>/dev/null || true

echo
echo "Done."
