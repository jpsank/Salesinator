# deploy/compose — the v0.12 control-plane stack (P4)

`docker-compose.yml` brings up the v0.12 control plane: the infra (`postgres:17-alpine`,
`valkey/valkey:8-alpine`, `minio` + `minio-init`, `ollama` + the one-shot `ollama-pull` — a local,
open-source completion endpoint for the live meeting copilot's card-tagging beats, see
`docs/docs/configuration.mdx`'s "Meeting copilot's live card-tagging model" section) and the
long-running services below, each building its own slim image from `<service>/Dockerfile`:

| service      | build context                          | host port | entrypoint                         |
|--------------|----------------------------------------|-----------|------------------------------------|
| admin-api    | `core/identity/services/admin-api`     | 18057     | `python -m admin_api`              |
| runtime      | `core/runtime`                         | 18090     | `python -m runtime_kernel`         |
| meeting-api  | `core/meetings/services/meeting-api`   | 18080     | `python -m meeting_api`            |
| agent-api    | `core/agent/services/agent-api`        | 18100     | `uvicorn control_plane.api`        |
| gateway      | `core/gateway/services/gateway`        | 18056     | `python -m gateway`                |
| terminal     | `clients/terminal`                     | 13000     | Next.js custom server              |
| mcp          | `core/meetings/services/mcp`           | 18010     | the MCP transport                  |
| flows-api    | repo root, `core/flows/Dockerfile`     | 18200     | `python -m flows_integrations.flows_api` |
| flows-mailbox| repo root, `core/flows/Dockerfile`     | —         | `python -m flows_integrations.mailbox` (profile `mailbox`) |

### flows, and what it replaces

`flows-api` is the reaction engine's HTTP surface and one of the domains the MCP assembly asks for
a tool manifest (PRD decision 40): `mcp` fetches `/.well-known/mcp-tools.json` from it, so a stack
that runs flows serves flows' tools on the one MCP surface, and one that does not simply serves
fewer. Before this service existed the engine ran as HOST processes beside the stack and `mcp` was
pointed at the docker BRIDGE ADDRESS of that host lane — a host-specific IP written into a
deployment, for a service the deployment did not run. `FLOWS_API_URL` now defaults to
`http://flows-api:8200`; set it to point at a flows elsewhere, or set it EMPTY to run a deployment
that genuinely does not carry the domain.

An existing deployment that reaches flows through such a bridge keeps working: its
`VEXA_FLOWS_API_URL` override still wins over the new default, so the host lane and any listener
in front of it retire on the operator's own schedule, after the stack is cut over — not on the day
this merges.

`flows-mailbox` is the inbound mail lane (IMAP poll → `POST /events`), the same image under a
different command and with the same environment. It is behind the `mailbox` COMPOSE PROFILE and
therefore off by default, because mail is an optional intake: a lane started without real IMAP
credentials restart-loops and reads as a broken stack. Turn it on with `--profile mailbox` (or
`COMPOSE_PROFILES=mailbox`) once `VEXA_MAIL_ADDR` and `VEXA_MAIL_APP_PASSWORD` are set. Do not
scale it — the IMAP cursor is single-writer by design.

Two things flows will refuse, and both are deliberate: it will not start without
`VEXA_FLOWS_API_KEY`, `VEXA_FLOWS_ADMIN_KEY` and `INTERNAL_API_SECRET` (a weak default makes an
unconfigured deployment look configured), and it will refuse to compose a mailed link when
`VEXA_UI_URL` is unset — at the link, not at boot, because a deployment may legitimately have no
terminal. Every key it reads is declared in `core/flows/src/config.v1.json` and checked against
this file by `gate:config-contract`.

Every service answers `GET /health` and carries a compose healthcheck; `depends_on` waits on
`condition: service_healthy` so the bring-up is ordered. The `runtime` mounts
`/var/run/docker.sock` and spawns the bot (`BROWSER_IMAGE=vexaai/vexa-bot:v012`, published — a
reference, never built here; never point it at the published `vexaai/vexa-bot:dev`, which is the
old 0.10 line and incompatible with this stack's `lifecycle.v1`) on demand and the per-dispatch
agent worker (`vexaai/v012-agent-worker:v012`, a `build-only` compose profile); neither is a
long-running compose service.

## The bot image

`redeploy.sh` also keeps the meeting-bot image current. The runtime spawns bots from `BROWSER_IMAGE`; when that is a locally
built tag (not a published `vexaai/` one) `bot-image.sh` compares a fingerprint of the sources the bot Dockerfile copies in
(all of `core/` plus the workspace manifests, uncommitted edits included) with the `vexa.bot-source` label on the image, and
rebuilds it with `make bot` only when they differ. An unchanged tree is a no-op; the next bot uses the new image, bots already
running keep the old one. A published `BROWSER_IMAGE` is left alone. `deploy/bin/redeploy-bot.sh` is a different, upstream
script: it rebuilds the published tag `vexaai/vexa-bot:v012`, which this stack's runtime does not spawn from, so on this deployment use
`redeploy.sh` (or `make bot BOT_IMAGE=…`) instead.

## Keeping the Docker disk from filling

`redeploy.sh` and `restart-service.sh` both source `docker-hygiene.sh`. Before building, if the Docker VM disk has
under 8 GB free, they drop the build cache, and refuse to build if less than 3 GB is still free (a full disk takes
Postgres down — it also shows up as `apt` "invalid signature" in builds). After building, they trim build cache
older than 72 h. Tune with `HYGIENE_MIN_FREE_GB`, `HYGIENE_ABORT_FREE_GB` and `HYGIENE_KEEP_HOURS`.
`make dev` builds a second, `:dev`-tagged copy of every image next to the `:v012` set; the next `redeploy.sh` or
`restart-service.sh` removes that copy when no container uses it. The compose gate removes its own containers, volumes and images.

## Usage

```bash
cp .env.example .env            # edit secrets/ports/DOCKER_GID
docker compose -f deploy/compose/docker-compose.yml build
docker compose -f deploy/compose/docker-compose.yml up -d
# poll until healthy, then:
curl -sf http://localhost:18056/health   # gateway
docker compose -f deploy/compose/docker-compose.yml down -v
```

`.env.example` documents every variable (faithful to the 0.11 `deploy/compose` names: `DB_*`,
`REDIS_URL`, `ADMIN_TOKEN`, `INTERNAL_API_SECRET`, `MINIO_*`, `BROWSER_IMAGE`/`AGENT_IMAGE`,
`DOCKER_GID`, `*_HOST_PORT`).

## Local hybrid dev — native agent-api + native Ollama, Docker for the rest

For fast iteration on `core/agent` code, or to get real GPU-accelerated local-model inference on
Apple Silicon (Docker Desktop's Linux VM has no Metal passthrough — a containerized Ollama is
CPU-only, period): run `agent-api` and Ollama as native host processes, keep everything else on
Docker. `runtime`'s bot/worker spawning is the actual per-turn isolation boundary and isn't a fit
for this — it stays containerized regardless.

**One-time setup:**
```bash
brew install ollama
mkdir -p ~/vexa-data/agent-workspaces
docker run --rm -v vexa-v012_agent-workspaces:/from:ro -v ~/vexa-data/agent-workspaces:/to \
  alpine cp -a /from/. /to/                    # migrate real workspace data out of the named volume
```
Add to `.env`:
```
AGENT_API_URL=http://host.docker.internal:18100
SALES_CYCLE_AGENT_API_INTERNAL_URL=http://host.docker.internal:18100
```

**Every day:**
```bash
./dev-up.sh                       # starts Docker (minus agent-api/ollama) + both native processes
./dev-down.sh                     # stops the native processes; Docker keeps running (fast restart)
./dev-down.sh --docker            # stops everything
```

Iterating on `core/agent` code is then: edit → `./dev-down.sh && ./dev-up.sh` (or just re-run
`./run-agent-api-native.sh` directly) — no image rebuild, no stale-container risk from forgetting
to respawn a long-running worker after a rebuild (the mistake that cost a live test earlier — a
worker container holding old code in memory across a rebuild it was never told about).

Logs: `~/vexa-data/logs/{agent-api,ollama}.log`. PIDs: `~/vexa-data/pids/`.

## Reaching a local stack from outside: use a NAMED Cloudflare Tunnel, not a quick one

A `cloudflared tunnel --url ...` quick tunnel gets a throwaway `*.trycloudflare.com` URL that
changes every restart, silently breaking any OAuth redirect URI or webhook Request URL pointed at
it (reproduced live — see the `webhook_url` gotcha in `integrations/sales-cycle/README.md`). For
anything longer-lived, use a **named** tunnel instead: `cloudflared tunnel login` once, then
`cloudflared tunnel create <name>`, with a `~/.cloudflared/config.yml` `ingress` list routing
subdomains to local ports (one tunnel can cover several services at once). Two snags hit live and
worth knowing up front: `sudo cloudflared service install` (no token arg) has produced a macOS
LaunchDaemon plist missing the `tunnel run` subcommand entirely — check
`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist` if the service seems to do nothing; and
if connections drop often, try `protocol: http2` in `config.yml` before assuming the tunnel itself
is unstable (a laptop's lid-close deep-idle state can look identical — check `pmset -g log`).

## What runs on this machine, and how to set it up again

One page for "I got a new Mac / something died — what was set up?". Each row says how the piece runs, the command that
(re)creates it, and where its own setup is written down.

| Piece | How it runs | (Re)create it | Setup written down in |
|---|---|---|---|
| The Docker stack | `docker compose` in this folder | `./redeploy.sh` rebuilds what changed and restarts only that; `./restart-service.sh <svc>` for one service | this file; secrets live in `.env` (gitignored, from `.env.example`) |
| agent-api + Ollama | **native** processes, never in Docker | `./dev-up.sh` (see "Local hybrid dev" above) | this file |
| Docker disk guard | runs inside `redeploy.sh` / `restart-service.sh` | automatic; `docker-hygiene.sh` refuses to build under ~3 GB free | "Keeping the Docker disk from filling" above |
| Meeting-bot image | rebuilt by `redeploy.sh` when its sources change | `./bot-image.sh` | "The bot image" above |
| Public tunnel | a root **LaunchDaemon**, `com.cloudflare.cloudflared`, running `cloudflared --config ~/.cloudflared/config.yml tunnel run` — it starts at boot and reads `config.yml` only when it starts | `cloudflared tunnel login` once, edit the `ingress` list in `config.yml`, then `sudo launchctl kickstart -k system/com.cloudflare.cloudflared` (drops the public addresses for a few seconds). Do not also start your own `cloudflared tunnel run`: a second connector splits requests between two configs | "Reaching a local stack from outside" above |
| Slack app (cards, votes, leader approval) | Slack calls `/slack/events` through the tunnel | follow the Slack steps; **Socket Mode must be OFF** | `integrations/sales-cycle/README.md` → Slack, "Approving by vote" |
| Zoom app (Connect Zoom) | Zoom calls the webhook through the tunnel | create it from `integrations/sales-cycle/zoom-app.manifest.json` (Develop → Build App → from an app manifest) | `integrations/sales-cycle/README.md` → Zoom |
| Product repo + GitHub token | stored by sales-cycle; the repo is this fork | Settings → Integrations → GitHub → Product repo → Change → **Use this repo** (this makes a *copy* of your GitHub token for the agent — redo it if pushes start failing with "expired") | `integrations/sales-cycle/README.md` → GitHub, "When the agent finishes but the branch cannot be pushed" |
| Vexa Capture (Mac app) | installed in `~/Applications`, opens at login | `clients/capture-mac/make-local-signing-cert.sh` once, then `./install.sh` | `clients/capture-mac/README.md` |
| Live previews | `preview-gate` container (restarts with Docker) + the runner, a native process that `dev-up.sh` and `redeploy.sh` start whenever `~/vexa-data/preview/gate.env` exists | `deploy/preview/preview.sh gate-up` once, then `./dev-up.sh` | `deploy/preview/README.md` |

The tunnel's hostnames today: `terminal.jsanker.com` → `localhost:13000`, `sales-cycle.jsanker.com` → `localhost:18300` (only its
intended public routes answer; the rest want the shared secret), `agent-api.jsanker.com` → `localhost:18100`; previews add
`*.jsanker.com` → `localhost:13100` (the preview gate, which turns away every host that is not a preview). `ingress` rules are matched
top to bottom, so the wildcard goes **after** the named hostnames and the `http_status:404` catch-all stays last. A quick tunnel
(`cloudflared tunnel --url …`) is for a one-off test only — its address changes on every restart and silently breaks Slack's Request URL.

## Smoke probe — "is this install actually working?"

```bash
make probe                       # from the repo root (compose is the default surface)
```

Drives the ONE full journey through the gateway front door — spawn → schedule → boot → join →
transcribe → live-view → stop — then sweeps every component's logs once. Each stage prints
Expected / Actual / Verdict; a red stage names where the journey broke and fails the command.
With the mock bot as `BROWSER_IMAGE` (`mock-bot:dev`) the journey is a deterministic green,
transcript included; with the real bot it drives a dead synthetic meeting to a truthful named
`join_failure`. See `deploy/compose/probe.sh` (a wrapper over `scripts/probe/journey.sh`).
