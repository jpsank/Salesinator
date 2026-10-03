# deploy/preview — live previews of proposed changes

A preview is the terminal built from a change, running on this machine, openable at
`preview-pr-<n>.<domain>` — so a salesperson can use the proposed change instead of reading code.
It shows the viewer's own real data and cannot change any of it.

```
./preview.sh gate-up            # once, and after editing gate.env
./preview.sh up <pr> [<ref>]    # build the terminal from <ref> (default HEAD) and run it as preview <pr>
./preview.sh list | down <pr> | gc [hours]
./preview.sh runner             # leave running: previews every pull request the agent opens
```

## From pull request to Slack link

`runner.mjs` polls sales-cycle (`GET /internal/previews/wanted`) for the agent's opened pull requests, fetches each
from GitHub, and builds a preview if the change touches `clients/terminal/` (the only thing a preview can show today;
anything else is reported `skipped`). It then reports (`POST /internal/previews/<id>`) and sales-cycle says it once in
the card's thread: a link when the preview is served at a public `https://` address, otherwise a plain note. A preview
that is only open on this machine is recorded and not announced.

## How a preview stays harmless

```
viewer ──► gate :8080 (front) ──► preview container ──► gate :8081/:8082 (guard) ──► gateway
           Cloudflare Access       no credentials,        viewer's own key,
           identity → email        private network       reads only
```

- **The preview holds no credential.** Its keys are placeholders; the browser's `vexa-token` cookie is an opaque
  session the gate signed (`gate/session.mjs`) and nothing else accepts.
- **Who is looking** comes from Cloudflare Access (`gate/access.mjs` verifies its signed JWT: signature, issuer,
  audience, expiry). The gate then looks that email up in Vexa — it never creates accounts — and mints that person a
  6-hour key (`gate/viewer.mjs`), so a preview shows exactly what they could already see.
- **Reads only.** The guard forwards `GET`/`HEAD` (and the `/ws` live-updates upgrade) to the gateway and refuses every
  other method with a plain message (`gate/policy.mjs`); the admin API answers one question — "who is this session?" —
  and nothing else. The agent API, sales-cycle, and capture are unreachable from a preview.
- **The container is boxed:** an `--internal` Docker network (reaches only the gate, no internet), read-only
  filesystem, all capabilities dropped, memory/CPU/pids limits.
- **Known limit:** a preview is built from the change's own source, including its `package.json`. Previews are only for
  changes a human has approved for the agent to work on.

## Settings (`~/vexa-data/preview/gate.env`)

| Key | Meaning |
|---|---|
| `PREVIEW_DOMAIN` | previews live at `preview-pr-<n>.<domain>`; `localhost` for local use (Chrome resolves `*.localhost`) |
| `PREVIEW_ACCESS_TEAM`, `PREVIEW_ACCESS_AUD` | Cloudflare Access team name and the Access application's Audience tag — set these to go public |
| `PREVIEW_DEV_EMAIL` | local use only: act as this viewer when no Access team is set |

## Tests

`node --test deploy/preview/gate/` — session forgery, Access verification, read/write policy, the credential swap, and
that a preview never sees a real key.
