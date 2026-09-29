#!/bin/sh
# run-agent-api-native.sh — runs agent-api as a native host process instead of the docker-compose
# `agent-api` service, for fast local iteration (no image rebuild per code change) — see
# deploy/compose/README.md's "Local hybrid dev" section. Everything else (postgres, redis, runtime,
# meeting-api, admin-api, gateway, terminal, sales-cycle, ollama) stays on Docker: runtime's bot/
# worker spawning is the actual per-turn isolation boundary and isn't a fit for this, and the rest
# just isn't worth the same trade for a service you rarely touch.
#
# Prerequisites, one-time:
#   1. redis must publish its port (docker-compose.yml's redis service already does — restart it
#      once after pulling this change: `docker compose up -d redis`).
#   2. The workspace store must be a HOST PATH, not the docker named volume, so this process and
#      Docker-spawned workers see the same data. Migrate once:
#        mkdir -p ~/vexa-data/agent-workspaces
#        docker run --rm -v vexa-v012_agent-workspaces:/from:ro -v ~/vexa-data/agent-workspaces:/to \
#          alpine cp -a /from/. /to/
#   3. Point the containers that call agent-api at this native process instead, in .env:
#        AGENT_API_URL=http://host.docker.internal:18100
#        SALES_CYCLE_AGENT_API_INTERNAL_URL=http://host.docker.internal:18100
#      then `docker compose up -d gateway terminal sales-cycle` and
#      `docker compose stop agent-api` (the docker service; its definition stays for reverting).
#
# Usage: ./run-agent-api-native.sh   (from deploy/compose/, or anywhere — paths below are absolute)
set -eu

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$HOME/vexa-data/agent-api-native"
WORKSPACES="$HOME/vexa-data/agent-workspaces"

# Mirror the Docker image's contract layout with symlinks (idempotent) — contracts.py loads sealed
# schemas BY PATH, walking up to find `meetings/contracts/...` and `agent/contracts/...` as siblings
# of control_plane/, which only matches the image's vendored layout, not the real repo tree.
mkdir -p "$STAGE/meetings/contracts/transcript.v1" "$STAGE/agent/contracts/workspace.v1" \
  "$STAGE/agent/contracts/invoke.v1" "$STAGE/agent/contracts/unit.v1" \
  "$STAGE/agent/contracts/routine.v1" "$STAGE/agent/contracts/event.v1" "$STAGE/agent/contracts/tool.v1"
ln -sfn "$REPO/core/agent/shared" "$STAGE/shared"
ln -sfn "$REPO/core/agent/control_plane" "$STAGE/control_plane"
ln -sfn "$REPO/core/agent/contracts" "$STAGE/contracts"
ln -sfn "$REPO/core/agent/workspace-seeds" "$STAGE/workspace-seeds"
ln -sfn "$REPO/core/meetings/contracts/transcript.v1/transcript.schema.json" "$STAGE/meetings/contracts/transcript.v1/transcript.schema.json"
for d in workspace.v1 invoke.v1 unit.v1 routine.v1 event.v1 tool.v1; do
  f=$(basename "$d" .v1)
  ln -sfn "$REPO/core/agent/contracts/$d/$f.schema.json" "$STAGE/agent/contracts/$d/$f.schema.json"
done

cd "$STAGE"
export PYTHONPATH="$STAGE"
export VEXA_AGENT_API_PORT=18100
export VEXA_RUNTIME_API_URL=http://localhost:18090
export VEXA_MEETING_API_URL=http://localhost:18080
export VEXA_AGENT_API_SELF_URL=http://localhost:18100
export VEXA_AGENT_PROFILE=agent
export VEXA_WORKSPACES_DIR="$WORKSPACES"
export VEXA_ADMIN_API_URL=http://localhost:18057
export VEXA_AGENT_DEFAULT_SUBJECT=u_live
export VEXA_LLM_PROVIDER=openai-compat
export VEXA_LLM_BASE_URL=http://localhost:11434/v1   # native Ollama — see run docs for GPU setup
export VEXA_LLM_MODEL="${VEXA_LLM_MODEL:-llama3.2:3b}"
export VEXA_LLM_TIMEOUT_SEC=300
export VEXA_LOG_LEVEL=info
export VEXA_REDIS_URL=redis://localhost:16379/0
export VEXA_WORKSPACE_MOUNT_SOURCE="$WORKSPACES"
export VEXA_DISPATCH_SIGNING_KEY=dev-dispatch-signing-key
export VEXA_GATEWAY_URL=http://localhost:18056
export VEXA_TERMINAL_URL=http://localhost:13000
export SALES_CYCLE_WORKSPACE_RESOLVE=true
export HOST_CLAUDE_CREDENTIALS="${HOST_CLAUDE_CREDENTIALS:-$HOME/.claude/.credentials.json}"
# Secrets/tokens: read from deploy/compose/.env at run time rather than hardcoded here (never commit
# real values — this script itself has none).
env_val() { grep "^$1=" "$REPO/deploy/compose/.env" 2>/dev/null | tail -1 | cut -d= -f2-; }
export VEXA_INTERNAL_API_SECRET="${VEXA_INTERNAL_API_SECRET:-$(env_val INTERNAL_API_SECRET)}"
export VEXA_BOT_API_KEY="${VEXA_BOT_API_KEY:-$(env_val VEXA_BOT_API_KEY)}"
export VEXA_GITHUB_OAUTH_CLIENT_ID="${VEXA_GITHUB_OAUTH_CLIENT_ID:-$(env_val VEXA_GITHUB_OAUTH_CLIENT_ID)}"
export VEXA_GITHUB_OAUTH_CLIENT_SECRET="${VEXA_GITHUB_OAUTH_CLIENT_SECRET:-$(env_val VEXA_GITHUB_OAUTH_CLIENT_SECRET)}"
export VEXA_GITHUB_OAUTH_REDIRECT_URI="${VEXA_GITHUB_OAUTH_REDIRECT_URI:-http://localhost:18100/api/workspace/git-token/oauth/callback}"

exec "$REPO/core/agent/.venv/bin/uvicorn" control_plane.api:app --host 0.0.0.0 --port 18100
