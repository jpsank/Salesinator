#!/bin/sh
# dev-up.sh — starts the whole local hybrid dev stack: Docker for everything that either doesn't
# change often or needs real container isolation (runtime's bot/worker spawning), native processes
# for agent-api (fast iteration, no image rebuild per code change) and Ollama (real GPU acceleration —
# Docker Desktop's Linux VM has no Metal passthrough). See README.md's "Local hybrid dev" section.
#
# Idempotent: safe to re-run — skips a native process that's already answering its health check
# instead of starting a second one (which would just fail on the port already being taken, but
# confusingly, not obviously).
set -eu

CD="$(cd "$(dirname "$0")" && pwd)"
PIDDIR="$HOME/vexa-data/pids"
LOGDIR="$HOME/vexa-data/logs"
mkdir -p "$PIDDIR" "$LOGDIR"

is_up() {
  curl -s -o /dev/null --max-time 2 "$1" 2>/dev/null
}

wait_for() {
  # wait_for <url> <label> <max_seconds>
  i=0
  while [ "$i" -lt "$3" ]; do
    if is_up "$1"; then
      echo "  ✓ $2 is up"
      return 0
    fi
    i=$((i + 2))
    sleep 2
  done
  echo "  ✗ $2 did not come up within ${3}s — check $LOGDIR/$2.log" >&2
  return 1
}

start_native() {
  # start_native <name> <script> <health_url>
  name="$1"; script="$2"; url="$3"
  if is_up "$url"; then
    echo "  ✓ $name already running"
    return 0
  fi
  if [ -f "$PIDDIR/$name.pid" ] && kill -0 "$(cat "$PIDDIR/$name.pid")" 2>/dev/null; then
    echo "  ✓ $name already running (pid $(cat "$PIDDIR/$name.pid"))"
    return 0
  fi
  echo "  starting $name..."
  nohup "$CD/$script" >"$LOGDIR/$name.log" 2>&1 &
  echo $! >"$PIDDIR/$name.pid"
}

echo "== docker services (everything except agent-api / ollama / ollama-pull) =="
SERVICES="$(docker compose -f "$CD/docker-compose.yml" config --services | grep -v -e '^agent-api$' -e '^ollama$' -e '^ollama-pull$')"
# shellcheck disable=SC2086
docker compose -f "$CD/docker-compose.yml" up -d $SERVICES
docker compose -f "$CD/docker-compose.yml" stop agent-api ollama 2>/dev/null || true

if [ -f "$HOME/vexa-data/preview/gate.env" ]; then
  echo "== live-preview runner (previews are set up on this machine) =="
  start_native preview-runner run-preview-runner.sh ""
fi

echo "== native ollama (GPU) + native agent-api =="
# Independent processes — agent-api doesn't need ollama already answering to start itself (only to
# serve its first completion request, later, on its own retry path). Kick both off, THEN wait for
# both concurrently, instead of paying ollama's full startup wait before agent-api's even begins.
start_native ollama run-ollama-native.sh "http://localhost:11434"
start_native agent-api run-agent-api-native.sh "http://localhost:18100/health"
wait_for "http://localhost:11434" ollama 60 &
w1=$!
wait_for "http://localhost:18100/health" agent-api 30 &
w2=$!
ollama_ok=0; wait "$w1" || ollama_ok=$?
agent_api_ok=0; wait "$w2" || agent_api_ok=$?
[ "$ollama_ok" -eq 0 ] && [ "$agent_api_ok" -eq 0 ]

echo
echo "Stack up. Logs: $LOGDIR/{ollama,agent-api,preview-runner}.log — PIDs: $PIDDIR/"
echo "Stop everything with ./dev-down.sh"
