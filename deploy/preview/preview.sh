#!/bin/sh
# preview.sh — live previews of proposed changes, served from this machine.
#
#   ./preview.sh gate-up            start (or refresh) the gate: the one door between previews and the real stack
#   ./preview.sh up <pr> [<ref>]    build the terminal from <ref> (default HEAD) and run it as preview <pr>
#   ./preview.sh down <pr>          stop and remove preview <pr>
#   ./preview.sh list               running previews and what they were built from
#   ./preview.sh gc [<hours>]       remove previews older than <hours> (default 72)
#   ./preview.sh runner             build a preview for each pull request the agent opens, and say so in its Slack thread
#
# A preview is a copy of the terminal built from the change, run with NO credentials on a private network that
# reaches nothing but the gate. The gate checks who is looking (Cloudflare Access), lends the preview that
# person's own read-only view of the real data, and refuses every write. See README.md.
#
# Settings live in $PREVIEW_STATE/gate.env (written on first gate-up, edit and run gate-up again):
#   PREVIEW_DOMAIN       previews are served at preview-pr-<n>.<domain>   (default: localhost, for local use)
#   PREVIEW_ACCESS_TEAM  Cloudflare Access team name (the part before .cloudflareaccess.com)
#   PREVIEW_ACCESS_AUD   the Access application's Audience tag
#   PREVIEW_DEV_EMAIL    local use only: act as this viewer when no Access team is set
set -eu

CD="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$CD/../.." && pwd)"
. "$ROOT/deploy/compose/docker-hygiene.sh"
STATE="${PREVIEW_STATE:-$HOME/vexa-data/preview}"
PORT="${PREVIEW_PORT:-13100}"
STACK_NETWORK="${PREVIEW_STACK_NETWORK:-vexa-v012_vexa}"
NET=vexa-preview
GATE=preview-gate
IMAGE_REPO=vexa-preview/terminal

die() { echo "preview: $*" >&2; exit 1; }
need_pr() { case "${1:-}" in ''|*[!0-9]*) die "need a preview number (digits only)";; esac; }
setting() { sed -n "s/^$1=//p" "$STATE/gate.env" | tail -1; }

gate_up() {
  mkdir -p "$STATE"
  umask 077
  [ -f "$STATE/session.secret" ] || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$STATE/session.secret"
  if [ ! -f "$STATE/gate.env" ]; then
    printf 'PREVIEW_DOMAIN=localhost\nPREVIEW_ACCESS_TEAM=\nPREVIEW_ACCESS_AUD=\nPREVIEW_DEV_EMAIL=\n' > "$STATE/gate.env"
    echo "preview: wrote $STATE/gate.env (local defaults)"
  fi
  # Only the one credential the gate needs is read from the stack's .env — never the whole file.
  admin_key="$(sed -n 's/^ADMIN_TOKEN=//p' "$ROOT/deploy/compose/.env" | tail -1)"
  [ -n "$admin_key" ] || die "ADMIN_TOKEN is not set in deploy/compose/.env"
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --internal "$NET" >/dev/null
  docker build -q -t vexa-preview/gate "$CD/gate" >/dev/null
  docker rm -f "$GATE" >/dev/null 2>&1 || true
  PREVIEW_SESSION_SECRET="$(cat "$STATE/session.secret")" VEXA_ADMIN_API_KEY="$admin_key" \
  docker run -d --name "$GATE" --restart unless-stopped --network "$STACK_NETWORK" \
    --read-only --cap-drop ALL --security-opt no-new-privileges --memory 256m --pids-limit 128 \
    -p "127.0.0.1:$PORT:8080" \
    -e PREVIEW_SESSION_SECRET -e VEXA_ADMIN_API_KEY \
    -e VEXA_ADMIN_API_URL=http://admin-api:8001 -e GATEWAY_UPSTREAM=http://gateway:8000 \
    -e "PREVIEW_DOMAIN=$(setting PREVIEW_DOMAIN)" -e "PREVIEW_ACCESS_TEAM=$(setting PREVIEW_ACCESS_TEAM)" \
    -e "PREVIEW_ACCESS_AUD=$(setting PREVIEW_ACCESS_AUD)" -e "PREVIEW_DEV_EMAIL=$(setting PREVIEW_DEV_EMAIL)" \
    vexa-preview/gate >/dev/null
  docker network connect --alias "$GATE" "$NET" "$GATE"
  echo "preview: gate up on 127.0.0.1:$PORT (previews at preview-pr-<n>.$(setting PREVIEW_DOMAIN))"
}

preview_up() {
  need_pr "${1:-}"; n="$1"; ref="${2:-HEAD}"
  docker inspect "$GATE" >/dev/null 2>&1 || die "the gate is not running — ./preview.sh gate-up first"
  sha="$(git -C "$ROOT" rev-parse --verify "$ref^{commit}")" || die "unknown ref $ref"
  domain="$(setting PREVIEW_DOMAIN)"
  hygiene_preflight || exit 1
  src="$(mktemp -d)"; trap 'rm -rf "$src"' EXIT
  git -C "$ROOT" archive "$sha" clients/terminal | tar -x -C "$src"
  # Layers whose inputs did not change come from the build cache, so only what the change touched is rebuilt.
  docker build -q -t "$IMAGE_REPO:pr-$n" --label "vexa.preview=$n" --label "vexa.preview.sha=$sha" "$src/clients/terminal" >/dev/null
  docker rm -f "vexa-preview-pr-$n" >/dev/null 2>&1 || true
  case "$domain" in localhost) origin="http://preview-pr-$n.localhost:$PORT";; *) origin="https://preview-pr-$n.$domain";; esac
  # No credentials in here: the keys below are placeholders, the real ones live only in the gate.
  docker run -d --name "vexa-preview-pr-$n" --network "$NET" --restart on-failure:3 \
    --label "vexa.preview=$n" --label "vexa.preview.sha=$sha" \
    --read-only --tmpfs /tmp --tmpfs /app/.next/cache:uid=1000,gid=1000 --user 1000:1000 \
    --cap-drop ALL --security-opt no-new-privileges --memory 768m --cpus 1 --pids-limit 256 \
    -e NODE_ENV=production \
    -e "GATEWAY_URL=http://$GATE:8081" -e "VEXA_ADMIN_API_URL=http://$GATE:8082" \
    -e VEXA_ADMIN_API_KEY=preview-has-no-keys -e VEXA_INTERNAL_API_SECRET=preview-has-no-keys \
    -e AGENT_API_URL=http://preview-offline:1 -e SALES_CYCLE_URL=http://preview-offline:1 \
    -e CAPTURE_INGEST_UPSTREAM=ws://preview-offline:1 \
    -e "NEXTAUTH_SECRET=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')" -e "NEXTAUTH_URL=$origin" \
    "$IMAGE_REPO:pr-$n" >/dev/null
  hygiene_after
  echo "preview: pr-$n is up from $(echo "$sha" | cut -c1-8) — $origin"
}

preview_down() {
  need_pr "${1:-}"
  docker rm -f "vexa-preview-pr-$1" >/dev/null 2>&1 || true
  docker rmi "$IMAGE_REPO:pr-$1" >/dev/null 2>&1 || true
  echo "preview: pr-$1 removed"
}

preview_list() {
  docker ps -a --filter label=vexa.preview --format '{{.Names}}\t{{.Status}}\t{{.Label "vexa.preview.sha"}}' | awk -F'\t' '{printf "%s\t%s\tbuilt from %s\n", $1, $2, substr($3,1,8)}'
}

preview_gc() {
  hours="${1:-72}"; now="$(date +%s)"
  for n in $(docker ps -a --filter label=vexa.preview --format '{{.Label "vexa.preview"}}'); do
    started="$(docker inspect -f '{{.Created}}' "vexa-preview-pr-$n" | sed 's/\..*//')"
    age=$(( now - $(date -j -u -f '%Y-%m-%dT%H:%M:%S' "$started" +%s 2>/dev/null || date -u -d "$started" +%s) ))
    [ "$age" -gt $(( hours * 3600 )) ] && preview_down "$n"
  done
  return 0
}

case "${1:-}" in
  gate-up) gate_up ;;
  up) shift; preview_up "$@" ;;
  down) shift; preview_down "${1:-}" ;;
  list) preview_list ;;
  gc) shift; preview_gc "${1:-72}" ;;
  runner) exec node "$CD/runner.mjs" ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
