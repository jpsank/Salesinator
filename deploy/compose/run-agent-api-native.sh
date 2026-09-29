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

# Make the repo's agent packages importable as top-level modules under PYTHONPATH=$STAGE (idempotent).
# contracts/loader.py finds its own schemas by walking UP from `Path(__file__).resolve()` — .resolve()
# follows this symlink to the REAL file under core/agent/contracts/loader.py, whose real parents
# already include the real core/ (holding both meetings/contracts/... and agent/contracts/... as they
# actually sit in the repo tree) — so a bare symlink to the package is sufficient; no per-schema mirror
# tree is needed. Verified live: _repo_root() resolves to the real core/ and a schema load succeeds
# from a stage directory containing ONLY this one symlink.
mkdir -p "$STAGE"
ln -sfn "$REPO/core/agent/shared" "$STAGE/shared"
ln -sfn "$REPO/core/agent/control_plane" "$STAGE/control_plane"
ln -sfn "$REPO/core/agent/contracts" "$STAGE/contracts"
ln -sfn "$REPO/core/agent/workspace-seeds" "$STAGE/workspace-seeds"

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
# Same native/Docker split as VEXA_WORKER_REDIS_URL below, for the SAME reason, but this one feeds
# dispatch.py's MODEL_AUTH_ENV_ALLOWLIST loop (env-driven, not a Settings field) — a
# Docker-spawned worker's meeting-copilot completion call would otherwise try VEXA_LLM_BASE_URL's
# host-facing localhost:11434 and fail, since the worker's own localhost is itself, not this host.
# host.docker.internal, NOT ollama:11434 — Ollama runs NATIVELY too (this whole script's point),
# so the docker-network hostname doesn't resolve at all (that container's kept stopped). Got this
# wrong on the first pass — reproduced live: "Name or service not known" against ollama:11434.
export WORKER_VEXA_LLM_BASE_URL=http://host.docker.internal:11434/v1
export VEXA_LLM_MODEL="${VEXA_LLM_MODEL:-gemma4:latest}"
export VEXA_LLM_TIMEOUT_SEC=300
export VEXA_LOG_LEVEL=info
export VEXA_REDIS_URL=redis://localhost:16379/0
# The redis URL handed to a DISPATCHED WORKER's own env is DIFFERENT from agent-api's own — a
# worker runs inside Docker, where `localhost` is the container itself, not this host. Reproduced
# live: a real worker crashed on startup with ConnectionRefusedError against the host-facing URL
# above before this was added. See shared/config.py's worker_redis_url docstring.
export VEXA_WORKER_REDIS_URL=redis://redis:6379/0
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
