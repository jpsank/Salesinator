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

A `cloudflared tunnel --url ...` quick tunnel (no account, throwaway `*.trycloudflare.com` URL) is
fine for a five-minute check, but not for anything that outlives one `cloudflared` process: the URL
changes every restart, which silently breaks every OAuth redirect URI and webhook Request URL that
was pointed at the old one — reproduced live, repeatedly, in one session (a stale HubSpot OAuth
redirect, a stale Slack Events URL, and the calendar `webhook_url` gotcha documented in
`integrations/sales-cycle/README.md`).

A **named** tunnel (`cloudflared tunnel login` once, then `cloudflared tunnel create <name>`) gets a
real, stable hostname under a domain you control, and one tunnel config can route several
subdomains to several local ports at once — e.g. `terminal.example.com` → `:13000`,
`sales-cycle.example.com` → `:18300`, `agent-api.example.com` → `:18100` — in one
`~/.cloudflared/config.yml`:
```yaml
tunnel: <tunnel-id>
credentials-file: /Users/you/.cloudflared/<tunnel-id>.json
ingress:
  - hostname: terminal.example.com
    service: http://localhost:13000
  - hostname: sales-cycle.example.com
    service: http://localhost:18300
  - hostname: agent-api.example.com
    service: http://localhost:18100
  - service: http_status:404
```
Two things worth knowing before you do this:
- `sudo cloudflared service install` (no token argument) has produced a broken LaunchDaemon plist on
  macOS — `ProgramArguments` missing the `tunnel run` subcommand entirely, silently a no-op. Check
  `cat /Library/LaunchDaemons/com.cloudflare.cloudflared.plist` after installing; if it's just the
  bare binary path, fix it by hand to `cloudflared --config <path to config.yml above> tunnel run`.
- If the default QUIC protocol drops connections often (a laptop's WiFi power-saving / lid-close
  deep-idle state is a common cause — check `pmset -g log | grep Wake` for a wake event lining up
  with the drop before assuming it's the tunnel), add `protocol: http2` to `config.yml` — a
  persistent TCP connection tolerates brief interruptions better than QUIC's UDP-based one.

Once it's live, every OAuth redirect URI (GitHub, HubSpot, Slack, …) and webhook Request URL should
point at the new stable hostname instead of `localhost` or a quick tunnel's rotating one.

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
