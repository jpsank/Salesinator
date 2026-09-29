#!/bin/sh
# run-ollama-native.sh — runs Ollama as a native host process instead of the docker-compose `ollama`
# service, for real Apple Silicon GPU acceleration. Docker Desktop's Linux VM has no Metal passthrough,
# so the containerized ollama service is CPU-only, period — this is the only way to get GPU inference.
# See deploy/compose/README.md's "Local hybrid dev" section.
#
# Prerequisite, one-time: `brew install ollama`.
#
# Usage: ./run-ollama-native.sh   (foreground — Ctrl-C stops it; dev-up.sh backgrounds it itself)
set -eu

# Flash attention + q8_0 KV cache: brew's own recommended flags for Apple Silicon. Context 16384: the
# default (4096) silently truncates a real coding-agent/card-extraction system prompt + tool schema,
# which looks like a model failure (empty output, no tool call, nothing) until you raise it — verified
# live against a real local model. 0.0.0.0: without this, Docker containers reaching in via
# host.docker.internal get connection-refused even though `curl localhost:11434` from the Mac itself
# works fine — a process bound to 127.0.0.1 only answers the loopback interface.
export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
export OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV_CACHE_TYPE:-q8_0}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-16384}"
export OLLAMA_HOST="${OLLAMA_HOST:-0.0.0.0:11434}"

OLLAMA_BIN="$(command -v ollama || echo /opt/homebrew/opt/ollama/bin/ollama)"
if [ ! -x "$OLLAMA_BIN" ]; then
  echo "ollama not found — run: brew install ollama" >&2
  exit 1
fi

exec "$OLLAMA_BIN" serve
