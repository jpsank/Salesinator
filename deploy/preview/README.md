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

## Going public (once)

Previews are open only on this Mac until the gate knows who may look. Order matters — **protect first, expose second**: the gate refuses to
start for a public domain without a Cloudflare Access team, and nothing should resolve to it before Access is in front.

1. **Enable Zero Trust** (Cloudflare dashboard → Zero Trust). Pick a team name (`<team>.cloudflareaccess.com`) and the **Free** plan (up to 50 users;
   Cloudflare may ask for a card at signup). Until this is done every Access API call answers "Access is not enabled".
2. **An API token** (dash.cloudflare.com/profile/api-tokens → Create Token → Custom): account permissions *Access: Apps and Policies → Edit* and
   *Access: Organizations, Identity Providers, and Groups → Read*, limited to your account. Keep it out of chat and shell history: copy it, then
   `pbpaste > ~/.cloudflared/access-token && chmod 600 ~/.cloudflared/access-token`. Rotate it after setup — it is only needed to change Access.
3. **The Access application** for `preview-*.jsanker.com` (self-hosted; Access allows one wildcard per hostname label), with an *Allow* policy
   for the people who may open previews and the One-time PIN login method on. Each person also needs a **Vexa account under the same email** —
   the gate looks them up and never creates one, so a viewer without one is told to sign in to Vexa once first.
4. **Tell the gate**: put the application's *Audience tag* and your team name in `~/vexa-data/preview/gate.env` (`PREVIEW_ACCESS_AUD`, `PREVIEW_ACCESS_TEAM`),
   set `PREVIEW_DOMAIN=jsanker.com`, clear `PREVIEW_DEV_EMAIL`, then `./preview.sh gate-up`. (The dev viewer is honoured only when the domain is `localhost`.)
5. **Expose it**: one wildcard DNS record, `cloudflared tunnel route dns <tunnel> "*.jsanker.com"` (a proxied CNAME to `<tunnel id>.cfargotunnel.com`; your named
   records keep winning over it), and one ingress rule in `~/.cloudflared/config.yml` **after** the named hostnames and before the catch-all:
   ```yaml
   - hostname: "*.jsanker.com"
     service: http://localhost:13100
   ```
   Restart `cloudflared` (the terminal's public address drops for a few seconds). Free Universal SSL covers one subdomain level, which is why the
   address is `preview-pr-<n>.jsanker.com` and not `pr-<n>.preview.jsanker.com`.
6. **Check it**: an unauthenticated `curl -sI https://preview-pr-1.jsanker.com` must answer Cloudflare's login redirect, never the gate's own JSON.
   Then open a preview as an allowed person.

If a viewer sees "no Vexa account yet", they have not signed in to Vexa with that email. If a preview shows "The preview is not running", its container is gone
(`./preview.sh list`; `gc` removes previews older than 72 hours).

## What a preview cannot do (by design)

Every write is refused with a plain message and logged by the gate (`docker logs preview-gate` — look for `refused`). Sales-cycle, the agent API
and Vexa Capture are unreachable from a preview, so those screens show empty or error states there. Only changes under `clients/terminal/` can be previewed;
a change to a backend service is reported "no live preview" in the card's thread until previews run on their own demo backend.

## Settings (`~/vexa-data/preview/gate.env`)

| Key | Meaning |
|---|---|
| `PREVIEW_DOMAIN` | previews live at `preview-pr-<n>.<domain>`; `localhost` for local use (Chrome resolves `*.localhost`) |
| `PREVIEW_ACCESS_TEAM`, `PREVIEW_ACCESS_AUD` | Cloudflare Access team name and the Access application's Audience tag — set these to go public |
| `PREVIEW_DEV_EMAIL` | local use only: act as this viewer when no Access team is set |

## Tests

`node --test deploy/preview/gate/` — session forgery, Access verification, read/write policy, the credential swap, and
that a preview never sees a real key.
