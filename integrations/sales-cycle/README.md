# sales-cycle

This turns Vexa's meeting bot into a sales tool: it figures out which customer a call belongs to,
notices when a customer asks for a new feature, and gets that feature built, pushed to GitHub, and
opened as a pull request — all without anyone touching Vexa's own screen.

It's a separate add-on, not a change to Vexa itself. It talks to Vexa the same way any outside app
would — over its normal web API — plus a few small, optional, fully generic additions inside Vexa
that we added ourselves (details below).

## What it does, step by step

1. **Figure out which customer a call is for.**
   - The simple way: a rep types the company name in Slack (`/vexa-tag Acme`) before or during the
     call.
   - The automatic way: if the rep's calendar is connected to Vexa, we look at who was invited to the
     meeting, match their email domain against HubSpot, and tag it — no rep action needed.
2. **Notice feature requests live, during the call.** We taught Vexa's meeting copilot to recognize
   when a customer explicitly asks for something the product doesn't do yet, and surface it as its
   own tagged card the moment it's said (not buried in general notes, and not waiting for the call
   to end).
3. **Post it to Slack for a thumbs-up — same call, not after it.** A background watcher tails that
   call's live card stream and posts each feature request to Slack as soon as it appears. A rep or
   PM reacts with ✅ to approve it while the call is still going.
4. **Build it, push a branch, open a pull request.** Once approved, an AI coding agent implements the
   feature in the real product codebase, in its own isolated worktree, on its own branch; once it's
   done, the branch is pushed and a PR opens against your default branch — ready for a human to
   review, and for your preview-hosting platform to build one (most only build previews for PRs, not
   bare branches).

## How the pipeline is doing

Each request records when it was posted, approved, pushed and had its PR opened, plus the PR link. Read the
counts and timings with:

```bash
docker exec vexa-v012-sales-cycle-1 python -m sales_cycle.report            # everything
docker exec vexa-v012-sales-cycle-1 python -m sales_cycle.report --days 7   # the last week
```

It prints, as JSON: requests per status, the approval rate, the share that reached a PR or failed, how many
needed a retry, the median / p90 / max of each hop (posted→approved, approved→pushed, pushed→PR, posted→PR) and the
PR links. Requests from before the stage timestamps existed are counted but left out of the timings.

## What the internet can reach

This service has a public address because Slack, Zoom, HubSpot and the browser's OAuth redirects must reach it — and a public address
exposes every route, so `internal_auth.py` makes the split explicit. **Public:** `/health`; the OAuth `authorize` redirect and
`callback`; `/slack/events` and `/webhooks/*` (each verifies the sender's signature); `/tag` and `/dispatch` (they act only under a Vexa
API key the caller must hold). **Everything else needs `X-Internal-Secret`** — connection status/disconnect/paste-a-token, the Slack
channel settings, `/internal/*` (the sweeps) and `/zoom/*` — which only Vexa's Terminal and this stack's sweep loop hold
(`SALES_CYCLE_INTERNAL_SECRET`, the same value as `INTERNAL_API_SECRET`). A route added later is private until it is listed as public, and
with no secret configured the private routes answer 503 rather than open.

## The pieces (file map)

| File | What it does |
|---|---|
| `hubspot_client.py` | Looks up a company in HubSpot, by name or by email domain. |
| `resolver.py` | Ties a meeting to a customer's workspace, using HubSpot's answer. |
| `calendar_resolver.py` | The automatic version of the above — reads attendee emails straight off Vexa's own notification, no extra lookup needed. |
| `live_card_watcher.py` | Tails one call's live copilot-card stream for its whole duration and posts each `feature_request` to Slack the instant it appears. |
| `zoom_routes.py` / `zoom_oauth.py` / `zoom_join.py` / `zoom_verify.py` | "Connect Zoom": a rep's own Zoom account, the signed "meeting started" webhook, and sending the bot to a meeting they just started. |
| `zoom_check.py` | `python -m sales_cycle.zoom_check` — is Connect Zoom ready: settings, redirect address, and the public webhook handshake. |
| `internal_auth.py` | Which routes are public and which need the shared secret. |
| `report.py` | `python -m sales_cycle.report` — pipeline counts, per-hop timings and PR links from the store. |
| `store.py` | A small local database tracking which requests are pending, approved, or done. |
| `orchestrator.py` | Once approved: kicks off the AI coding turn (in its own isolated worktree), checks in until it's done, pushes it, then opens a pull request. |
| `slack_client.py` / `slack_verify.py` | Talking to Slack, and proving a Slack request is really from Slack. |
| `webhook_verify.py` | Proving a Vexa notification is really from Vexa. |
| `hubspot_oauth.py` / `slack_oauth.py` | The "Connect HubSpot" / "Connect Slack" OAuth2 dance — authorize URL, code exchange, (HubSpot only) token refresh. |
| `oauth_routes.py` | The one shared "Connect X" route shape (authorize/callback/status/disconnect/paste-a-token) HubSpot and Slack both register through. |
| `api.py` | The web addresses (endpoints) everything above is reachable at. |
| `settings.py` | Every knob you can configure (API keys, URLs, etc.), in one place. |

## Where things stand

All four stages described above are built and covered by tests (100+ tests as of this writing).
Every credential below is empty by default (the stack runs fine with none of it set — the add-on
just won't have anything to look up yet); here's how to get each one for real.

## Real-time capture: why a watcher, not a poll

Feature requests are captured LIVE, not after the call: Vexa's meeting copilot already surfaces
cards (transcript notes, tagged items — `feature_request` is one tag) on a per-meeting stream the
moment each one comes up, the same stream the Terminal's own live-call view renders from. For each
call, `live_card_watcher.py` is started as a background task the instant that call's
`meeting.started` webhook arrives, tails that one stream for the meeting's whole duration, and posts
each `feature_request` card to Slack as soon as it appears (the post and the "already seen" mark are
recorded together, and a failed write is logged without re-posting that card or ending the tail) — running as the call's own dispatching
user (`data.meeting.user_id` off that webhook), which is what both the stream's ownership check and
the workspace-binding lookup are keyed on. Workspace resolution happens per card, not once per
call, since a customer tag can land after the call — and its first few cards — already started.
`/internal/process-approved` still runs on a schedule, but only for what's left once a request is
approved: checking in on the AI agent's implementation turn and pushing the finished branch once
it's done (there's no event for "the agent finished" to react to instead).

**Two prerequisites this add-on doesn't control, both pre-existing Vexa machinery:** the meeting
copilot only tags cards for a call when processing is turned ON for it — the Terminal's live-call
view does this, but nothing in this add-on's own webhook handler does yet (a real gap: it should
start it automatically the moment a call begins, not depend on someone having that view open). And
the copilot's live card-tagging needs its own completion model configured — see
`docs/docs/configuration.mdx`'s "Meeting copilot's live card-tagging model" section; it defaults to
a local, open-source model via Ollama (already wired into `docker-compose.yml`), no external API
key required out of the box.

## One-time setup: connecting HubSpot, Slack, and GitHub

Every step below is also reachable interactively from Settings → Integrations in the Terminal UI
("Connect HubSpot" / "Connect Slack" / "Connect GitHub", with the product-repo picker folded into
that same GitHub card) once the corresponding env vars are set — this section is what to put IN
those env vars, and the couple of steps ("Event Subscriptions", the GitHub OAuth App) that can
only be done on the provider's own site.

### HubSpot

Two ways to authenticate — pick one, don't set both. **Both can be set up entirely from Settings →
Integrations → Sales Cycle in the Terminal UI** — the HubSpot card offers "Connect HubSpot" (OAuth)
and, right below it, "Or paste a Service Key / private-app token" (no env var editing needed for
either). What follows is how to GET the credential each path needs.

- **Service Key** (recommended — HubSpot disabled creating new legacy private apps on
  2026-09-28): HubSpot account → **Settings → Integrations → Service Keys** (or **Development →
  Keys → Service keys**) → create one, name it, grant it the `crm.objects.companies.read` scope (a
  Service Key can only be granted scopes YOU already have). Paste the generated token (`pat-...`)
  straight into the HubSpot card's "paste a token" field — or set
  `SALES_CYCLE_HUBSPOT_TOKEN=pat-...` in `.env` and restart sales-cycle, if you'd rather it be a
  deployment-wide default nobody has to re-enter.

- **OAuth** ("Connect HubSpot" in the browser) — **legacy public app creation is genuinely closed**
  for new accounts now (confirmed live: HubSpot's app-creation screen says *"New legacy public app
  creation is disabled. Run `hs project create` in the HubSpot CLI to build OAuth apps for multiple
  accounts."*). The working path is the CLI, not the web form:

  1. Install the [HubSpot CLI](https://developers.hubspot.com/docs/developer-tooling/local-development/hubspot-cli/install-the-cli)
     and make sure `hs account list` shows your account authenticated.
  2. Scaffold a project with OAuth auth, private distribution, no extra features:
     ```bash
     hs project create --name <your-app-name> --dest <some-dir> \
       --project-base app --distribution private --auth oauth --account <your-account>
     ```
     (When the interactive feature picker appears, just press Enter — select nothing.)
  3. Edit the generated `src/app/<name>/app-hsmeta.json`: set `auth.redirectUrls` to your real
     callback URL and `auth.requiredScopes` to `["oauth", "crm.objects.companies.read"]`:
     ```json
     "auth": {
       "type": "oauth",
       "redirectUrls": ["https://<your-public-host>/oauth/hubspot/callback"],
       "requiredScopes": ["oauth", "crm.objects.companies.read"]
     }
     ```
     **The redirect URL needs to be genuinely publicly reachable, not `localhost`** — this is the
     one we confirmed working (a Cloudflare quick tunnel, same tool/reason as Slack's Event
     Subscriptions further down); we didn't test whether a plain `localhost` URL is rejected
     outright or just untested for this app type, so don't assume it'll work.
  4. `hs project upload --account <your-account>` (confirm "create project" when prompted) —
     builds and deploys the app to your account.
  5. `hs project info --account <your-account>` prints the App ID, but **not** the Client ID/Secret
     — the CLI doesn't expose those. Open
     `https://app.hubspot.com/developer-projects/<account-id>/project/<project-name>`, find the
     app's **Auth** section, and copy the Client ID and Client Secret from there.
  6. Set:
     ```
     SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_ID=...
     SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_SECRET=...
     SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI=https://<your-public-host>/oauth/hubspot/callback
     ```

  One thing we could NOT confirm from docs and had to test live: HubSpot's newer **MCP server**
  OAuth (`mcp-*.hubspot.com/oauth/authorize/user`) requires PKCE and is a different, heavier
  protocol meant for AI-agent clients — don't use it here, it's not what sales-cycle needs. The app
  built via the steps above uses the classic `app.hubspot.com/oauth/authorize` endpoint; it worked
  for us at first **without** needing PKCE, then started requiring it — confirmed live by generating
  a verifier/challenge pair and retrying "Connect HubSpot". `hubspot_oauth.py` now sends PKCE
  (S256) on every authorize call, so this needs no per-deployment action — but HubSpot's platform
  has been changing fast enough this year that a PKCE-related error on "Connect HubSpot" is still
  worth knowing about if the flow ever misbehaves again.

### Slack

Unlike HubSpot, creating a Slack app is unavoidable either way (there's no static-token
equivalent of a HubSpot Service Key that skips it) — but once the app exists, you still have a
choice for the *credential* sales-cycle uses: the OAuth flow below (`SALES_CYCLE_SLACK_OAUTH_*`,
powers "Connect Slack" in the UI), or copy the same app's **Bot User OAuth Token** (`xoxb-...`,
shown after step 8) straight into `SALES_CYCLE_SLACK_BOT_TOKEN` and skip the OAuth client id/
secret/redirect steps entirely. Steps 1–2 and 4–8 (scopes, the tunnel, Event Subscriptions,
credentials, install) are needed either way — only step 3 (OAuth redirect URL) and the OAuth half
of step 9 are OAuth-specific.

1. `api.slack.com/apps` → **Create New App** → **From scratch**.
2. **OAuth & Permissions** → **Bot Token Scopes** → add `chat:write`, `channels:read`,
   `groups:read`, and **`reactions:read`** (required for the ✓-approval flow's `reaction_added`
   event to be delivered at all — Slack silently drops an event subscription the bot token
   doesn't hold the matching scope for). For approval by vote ("Approving by vote" below) also add
   **`reactions:write`** (the bot puts 👍/👎 on each card), and — only if you use those sources of
   leaders — **`users:read`** (workspace admins/owners) and **`usergroups:read`** (a user group).
3. **OAuth & Permissions** → **Redirect URLs** → add `http://localhost:18300/oauth/slack/callback`
   (fine as `localhost` for local use — it's your own browser that makes this request; when serving
   from a domain, add the public one too — see "Serving it from a domain" below).
4. **Give Slack a real address to reach `/slack/events` at.** Event Subscriptions is an INCOMING
   webhook — Slack's own servers make this request, and `localhost` means nothing to them (it
   only resolves to whatever machine is asking). If you're not already behind a real public
   domain, expose sales-cycle's port with a tunnel:
   ```bash
   cloudflared tunnel --url http://localhost:18300
   ```
   This prints a public `https://<random-words>.trycloudflare.com` URL — that's ephemeral, so if
   you restart `cloudflared` you'll get a new one and need to update step 5 below again. For a
   stable URL instead, create a named tunnel in the Cloudflare Zero Trust dashboard (Networks →
   Tunnels → your tunnel → Public Hostname → Service: HTTP, URL: `localhost:18300`) and pick a
   permanent hostname.
5. **Event Subscriptions** → toggle on → **Request URL**: `<your tunnel or domain>/slack/events`
   (sales-cycle answers Slack's verification handshake automatically — you should see a green
   checkmark within a couple seconds) → under **Subscribe to bot events** add `reaction_added`
   and `reaction_removed` (the second lets a taken-back 👎 count at once; without it the sweep
   catches it within seconds) → save. **This step has no API — it can only be done here, by hand, once (and again each time an
   ephemeral tunnel URL changes).**
6. **Basic Information → App Credentials**: copy Client ID, Client Secret, and Signing Secret
   (three separate values — the signing secret verifies incoming Slack requests, unrelated to the
   OAuth pair).
7. Right-click the channel feature requests should post to → **View channel details** → copy its
   ID.
8. **Install to Workspace** (top of the app config) — required any time scopes change. This is
   also where the **Bot User OAuth Token** (`xoxb-...`) appears, if you're using that instead of
   the OAuth client id/secret.
9. Set (always needed):
   ```
   SALES_CYCLE_SLACK_SIGNING_SECRET=...
   SALES_CYCLE_SLACK_CHANNEL_ID=...
   ```
   plus **either**:
   ```
   SALES_CYCLE_SLACK_OAUTH_CLIENT_ID=...
   SALES_CYCLE_SLACK_OAUTH_CLIENT_SECRET=...
   ```
   **or**:
   ```
   SALES_CYCLE_SLACK_BOT_TOKEN=xoxb-...
   ```

### GitHub (personal tokens AND the product repo — one shared OAuth App)

This is a Vexa-wide setting (`VEXA_GITHUB_OAUTH_*`), not sales-cycle-specific — it also powers
every rep's own "Connect GitHub" token card.

1. GitHub → **Settings → Developer settings → OAuth Apps → New OAuth App**.
2. Homepage URL: your Terminal's URL (`http://localhost:13000` locally).
3. **Authorization callback URL** (must match exactly):
   `http://localhost:18100/api/workspace/git-token/oauth/callback`
4. Generate a client secret, copy both it and the Client ID.
5. Set:
   ```
   VEXA_GITHUB_OAUTH_CLIENT_ID=...
   VEXA_GITHUB_OAUTH_CLIENT_SECRET=...
   ```
   (`VEXA_GITHUB_OAUTH_REDIRECT_URI` / `VEXA_TERMINAL_URL` already default correctly for local dev; behind a domain, set both to your public URLs — see "Serving it from a domain".)

The card asks GitHub whether the saved token still works each time it loads (`GET /api/workspace/git-token?verify=true`);
when GitHub rejects it, the card says so and offers **Reconnect**, and hides the repo picker until it is fixed. No verdict
(a GitHub outage or rate limit) is never treated as a rejection. If GitHub revokes or expires a saved token, loading the repo list returns `409` ("GitHub rejected
your saved token … disconnect and reconnect GitHub") and the card shows that message — reconnect to fix
it. A GitHub outage or other upstream failure stays a `502`. When a rep picks the product repo, their
*saved* token is copied to the shared `product-repo` identity; a one-time token sent with the request
is used for that call only and never stored.

### Serving it from a domain

The Connect buttons are real browser redirects, so every address in the OAuth round trip has to be
one the browser can reach. Four settings carry them, and they are independent — setting one does not
change the others:

| Setting | What it is | Default |
|---|---|---|
| `SALES_CYCLE_BROWSER_URL` | Where the terminal's "Connect HubSpot/Slack" button sends the browser. Baked into the **terminal image at build time** — `./redeploy.sh` rebuilds it; a plain `docker compose up` does not. Only the terminal reads it. | `http://localhost:${SALES_CYCLE_PORT}` |
| `SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI` / `SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI` | Where HubSpot/Slack send the browser after consent. Must match the redirect URL registered in the provider's app **exactly**. | `http://localhost:18300/oauth/<provider>/callback` |
| `SALES_CYCLE_TERMINAL_URL` | Where the browser lands once the connection is saved. | `http://localhost:13000` |

For `https://sales-cycle.example.com` and `https://terminal.example.com`:
```
SALES_CYCLE_BROWSER_URL=https://sales-cycle.example.com
SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI=https://sales-cycle.example.com/oauth/hubspot/callback
SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI=https://sales-cycle.example.com/oauth/slack/callback
SALES_CYCLE_TERMINAL_URL=https://terminal.example.com
```
GitHub is configured separately (`VEXA_GITHUB_OAUTH_REDIRECT_URI` and `VEXA_TERMINAL_URL`, above).
When running agent-api natively via `run-agent-api-native.sh`, it reads both from `.env`
(falling back to localhost), so they follow the same file.

### Approving by vote (the team votes, a leader gives the go-ahead)

By default **anyone's** ✅ on a feature-request card approves it. In Settings → Integrations → Slack, **Who can approve** changes that:

- **Leaders** are anyone matching *any* of: a list of Slack member ids (profile → ⋮ → *Copy member ID*, `U…`), **workspace admins and
  owners**, and members of one or more Slack **user groups** (`S…`). Set nothing and the original rule (anyone's ✅) stays.
- Each card is posted with 👍 and 👎 already on it. The team votes by clicking them.
- A **leader's ✅ approves the request only once 👍 outnumber 👎.** Counting: the bot's own seeded reactions are not votes; someone who
  reacted both 👍 and 👎 has voted neither way; a ✅ from a non-leader counts as a 👍; a leader's ✅ is the go-ahead, not a vote. The
  reactions are recounted from Slack each time, never trusted from the event.
- On approval the bot replies in the card's thread with who approved and the tally, and the agent starts exactly as before. If a leader
  said go before the votes were there, the bot says so once and the request is approved **on its own** the moment 👍 outnumber 👎
  (the sweep re-checks it every few seconds, so it does not depend on Slack delivering a `reaction_removed`).
- A failed Slack lookup never grants approval (a missing scope, an outage): nobody becomes a leader by an error.

Needs the extra scopes above and a **reconnect** of Slack in Settings after adding them. The approval records who approved and the
tally (`approved_by`, `votes_up`, `votes_down`); older requests keep empty values.

### Zoom (the bot joins every call you start, scheduled or not)

Each rep clicks **Connect Zoom** (Settings → Integrations → Zoom) and approves on Zoom's own screen. From then on,
when that rep **starts a meeting** — scheduled, instant, or in their personal room — Zoom tells this service, which
fetches the meeting's join link and sends Vexa's bot, exactly as if the rep had added the bot by hand (their quotas,
webhooks and recording defaults apply, and a meeting that already has a bot is not joined twice). Disconnecting
stops it. Only meetings the connected rep **hosts** trigger it — Zoom does not announce a customer's meeting you
merely attend. The bot is a visible participant and, as a guest, waits in the waiting room unless the host admits it
or the waiting room is off.

Connecting also mints the rep a bot-scoped Vexa API key named `zoom-auto-join` (it appears in their Tokens panel):
that is what this service sends the bot with, and disconnecting revokes it. This service is reachable from the
internet, so the per-rep endpoints (`/zoom/*`) refuse anyone without `SALES_CYCLE_INTERNAL_SECRET` — compose sets it
to the same `INTERNAL_API_SECRET` the Terminal presents; only `/oauth/zoom/callback` (protected by a one-time state) and
`/webhooks/zoom` (protected by Zoom's signature) are meant to be public.

**Faster: upload the manifest.** In Zoom, *Develop → Build from an app manifest*, upload `zoom-app.manifest.json` (change the host in it
first if your sales-cycle address is not `sales-cycle.jsanker.com`), and Create. That does steps 1–3 below — the redirect URL, the two scopes
and the *Start Meeting* subscription — and keeps the app **unlisted** (not in the Marketplace; only your Zoom account can authorize it).
Zoom still generates the Client ID, Client Secret and event Secret Token itself, so step 4 stays yours. If Zoom rejects the file for a
missing section, download Zoom's own template from the same screen and merge the two. If Zoom will not create the app because it cannot
validate the webhook yet (it can only answer once the secret token is in `.env`), set `event_subscription.enable` to `false`, create the
app, do step 4, then upload the manifest again with it `true`. `tests/test_zoom_manifest.py` keeps the file in step with the routes and scopes.

One-time setup, by whoever operates the deployment, at [marketplace.zoom.us](https://marketplace.zoom.us) (Zoom's
console changes names often — the app is a **user-managed OAuth app**, called a "General App" in the current one):

1. **Develop → Build App**, user-managed. Add the redirect URL
   `https://<your sales-cycle public host>/oauth/zoom/callback` (and to the OAuth allow list if asked).
2. **Scopes:** the ones Zoom labels *View a meeting* (`meeting:read:meeting`) and *View a user* (`user:read:user`).
3. **Event Subscriptions:** on; the endpoint URL is `https://<your sales-cycle public host>/webhooks/zoom`; subscribe to
   the meeting event **Start Meeting** (`meeting.started`). Optionally set the same URL as the deauthorization endpoint.
4. Put the app's credentials in `.env` and redeploy, **then** click *Validate* on the endpoint URL (Zoom sends a
   handshake this service can only answer once it has the secret token):
   ```
   SALES_CYCLE_ZOOM_OAUTH_CLIENT_ID=...          # the app's Client ID
   SALES_CYCLE_ZOOM_OAUTH_CLIENT_SECRET=...      # the app's Client Secret
   SALES_CYCLE_ZOOM_OAUTH_REDIRECT_URI=https://<host>/oauth/zoom/callback
   SALES_CYCLE_ZOOM_WEBHOOK_SECRET_TOKEN=...     # Event Subscriptions → Secret Token
   ```
5. **Check it before clicking Validate:** `docker exec <sales-cycle container> python -m sales_cycle.zoom_check` confirms the
   settings are present, the redirect address has the shape Zoom needs, and the public address answers the validation
   handshake with the configured secret token, refuses an unsigned notification, and refuses the per-rep routes without the
   internal secret (no secret is printed; `--base` probes another address). It cannot prove Zoom accepts the client id or
   the fields of a real `meeting.started` — that is the first real call.
6. Until the app is published, only users in the **same Zoom account** can authorize it — enough for your own
   company; another team self-hosting registers its own app. Zoom starts and stops delivering a connected user's
   events about a minute after they connect or disconnect.

The Zoom card shows the rep's connection, the last meeting the bot was sent to and what happened (joined, a bot was
already there, a failure with its reason), and warns if the webhook secret is missing.

### The product repo

The "build it and push a branch" step runs as one dedicated Vexa **subject** — just a slug
(default `product-repo`), not a real login — whose own workspace is your actual product repo.

**Preferred: the UI, and there's no separate connection to make.** Open Settings → Integrations →
**GitHub** (the same personal card, not a second one) → **Connect GitHub** → once connected, a
**Product repo** section appears right there in that same card → pick your repo from the dropdown.
No clone URL typed, no token pasted, and no second "Connect GitHub" click for a separate identity
— whoever's GitHub is already connected on that card is who authenticates the attach, and a copy
of that token is kept server-side under the `product-repo` subject too, so later pushes (which run
as that subject, not as whoever set it up) keep working on their own.

The product repo is one deployment-wide setting, so **only an admin** sees the picker and may act as the
`product-repo` subject: the terminal's workspace proxy refuses `?for=` and `for_subject` from anyone else
(`403`). That gate lives in the terminal, where admin status is known; agent-api itself only checks that the
name equals the configured `VEXA_WORKSPACE_DELEGATE_SUBJECT`, so a caller holding a Vexa API key who talks to
the gateway directly is not stopped by it — keep API keys to people you would trust with the product repo.

**Fallback: curl**, for a scripted/headless setup (attaches the repo the exact same way the UI
does, just called directly with an explicit token since there's no browser session to source one
from):
```bash
curl -X POST "$AGENT_API_URL/api/workspace/swap" \
  -H "X-User-Id: product-repo" -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/your-org/your-product.git", "ref": "main", "token": "<a push-capable GitHub token>"}'
```

`SALES_CYCLE_PRODUCT_REPO_SUBJECT` (this add-on) and agent-api's `VEXA_WORKSPACE_DELEGATE_SUBJECT`
(`core/agent/shared/config.py`) both default to `product-repo` and **must name the same subject**:
this add-on uses its copy to know who to dispatch/push builds as; agent-api uses its copy to know
which subject the picker (and the curl fallback above) is allowed to act on behalf of via
`for=`/`for_subject`. If you ever change one, change both.

**Every implementation turn gets its own isolated `git worktree`** (agent-api's
`isolation.mode: "worktree"`, opt-in per dispatch) — without it, two feature requests approved close
together would run their `git checkout -b`/`git commit` against the SAME shared directory and
corrupt each other. This is automatic; nothing to configure. The unit id names the worktree directory,
so it must be a single path component (anything else is refused with a 400), and a worktree whose
pull request never opened is released after three days, the next time one is provisioned for that
subject. Each dispatch attempt also gets its own branch (`feature/<title-slug>-<short random suffix>`),
so a retry, or two requests with the same title, never collide on a branch name.

**Attribution — optional, but worth setting if the product repo is one you (or your org) actually
maintain**, e.g. if you're dogfooding SalesCycle on Vexa itself:
```
SALES_CYCLE_PRODUCT_REPO_SIGNOFF_NAME=Your Name
SALES_CYCLE_PRODUCT_REPO_SIGNOFF_EMAIL=you@yourcompany.com
```
Both empty (default) — commits carry no signoff, no different from any other automated commit. Both
set — every commit an implementation turn makes gets a proper `Signed-off-by: Your Name <you@…>`
line (agent-api writes that identity into the product repo's own git config and installs the standard `prepare-commit-msg` hook, once per subject — the hook reads that repo-level identity, since a worker container has no global git config) and never a
`Co-Authored-By: Claude` trailer, and the push itself refuses (409, nothing pushed) if either check
fails. If the product repo has its OWN `pre-push` git hook configured (any repo with a normal
contribution process might), that hook runs too, the same as it would for a human's local
`git push` — a genuinely broken change gets refused before it reaches GitHub, not just committed.
The hook runs with a minimal environment (toolchain, proxy and git-identity variables — never
agent-api's own secrets) and is cut off after ten minutes, which counts as a failed check. The same
minimal environment applies to `VEXA_WORKSPACE_WORKTREE_SETUP_CMD`; name any extra variable it needs
(such as a private-registry token) in `VEXA_WORKSPACE_WORKTREE_SETUP_ENV`.

**A pull request opens automatically once a branch is pushed** (`SALES_CYCLE_PRODUCT_REPO_DEFAULT_BRANCH`,
default `main`) — not just for human review: most preview-hosting platforms (Vercel, Netlify, …)
only build a preview for a pull request, not a bare pushed branch, so this is also what makes the
"live preview" half of the pipeline actually fire.

## The small additions inside Vexa itself

Everything above talks to Vexa purely through its existing, public web API — except three small,
generic additions to agent-api (none of them Vexa-specific, none of them assume anything about
what "the product repo" is):

- Vexa's meeting-notes agent didn't have a way to know which customer's notes-folder it should
  write into. `core/agent/control_plane/transcription_watcher.py`'s `SALES_CYCLE_WORKSPACE_RESOLVE`
  (on by default) reads "which customer is this meeting tagged as" and uses that instead of writing
  every single call's notes into one shared folder. A rep may tag a call after it starts, so until a
  tag is found the meeting owner's folder is only a provisional answer and is re-checked (throttled)
  rather than fixed for the whole call. For any meeting that isn't tagged, it falls
  straight through to the old behavior — proved by running Vexa's own full test suite with the flag
  both on and off.
- Per-turn worktree isolation (`isolation.mode: "worktree"` on a unit.v1 dispatch,
  `core/agent/control_plane/workspace_worktree.py`) — an opt-in, backward-compatible schema
  addition; every dispatch that doesn't ask for it is byte-identical to before.
- The signoff/no-trailer compliance check + pre-push-hook runner
  (`core/agent/control_plane/gates_runner.py`) and pull-request creation
  (`core/agent/control_plane/workspace_publish.py`'s `create_pull_request`,
  `POST /api/workspace/pull-request`) — both only run when a caller opts into the isolated-worktree
  path; the plain interactive Settings-page push is untouched.

## Calendar auto-mapping: the webhook registers itself

The calendar path (candidate-domain matching against HubSpot) needs Vexa's `meeting.started`
webhook (docs/docs/webhooks.mdx) pointed at this add-on. Vexa itself has no settings UI for that —
it's normally a one-time `PUT /user/webhook` API call per account. The Terminal's "Connect
calendar" flow now does this automatically the first time a rep connects a calendar
(`clients/terminal/src/app/api/calendar/register-webhook/route.ts`), using
`SALES_CYCLE_CALENDAR_WEBHOOK_SECRET` as the shared secret. It never overwrites a webhook a rep
already has configured for something else — it only fills the setting in when it's empty or
already points here.

**Gotcha — this URL lives in Postgres, not `.env`.** It's stored per-account in `users.data.webhook_url`
the ONE time "Connect calendar" runs; changing `SALES_CYCLE_TERMINAL_URL` / your public domain
afterward does nothing to it. Reproduced live: after moving from a quick Cloudflare tunnel to a
stable named one, every `meeting.started` delivery kept silently POSTing to the OLD dead tunnel
URL — no error anywhere in the chain, the meeting-copilot tagged real `feature_request` cards
correctly, they just never reached the watcher that posts them to Slack. There's no UI to fix
this today; update it directly:
```sql
UPDATE users SET data = jsonb_set(data, '{webhook_url}', '"https://<new-public-host>/webhooks/meeting-started"') WHERE id = <user_id>;
```
If you change your public domain, do this for every account that already ran "Connect calendar" —
don't assume a fresh deploy or a new tunnel carries it forward.
