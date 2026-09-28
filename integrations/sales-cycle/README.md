# sales-cycle

This turns Vexa's meeting bot into a sales tool: it figures out which customer a call belongs to,
notices when a customer asks for a new feature, and gets that feature built and pushed to GitHub for
review — all without anyone touching Vexa's own screen.

It's a separate add-on, not a change to Vexa itself. It talks to Vexa the same way any outside app
would — over its normal web API — plus one small, optional addition inside Vexa that we added
ourselves (details below).

## What it does, step by step

1. **Figure out which customer a call is for.**
   - The simple way: a rep types the company name in Slack (`/vexa-tag Acme`) before or during the
     call.
   - The automatic way: if the rep's calendar is connected to Vexa, we look at who was invited to the
     meeting, match their email domain against HubSpot, and tag it — no rep action needed.
2. **Notice feature requests during the call.** We taught Vexa's meeting notes to recognize when a
   customer explicitly asks for something the product doesn't do yet, and write it down as its own
   item (not buried in general notes).
3. **Post it to Slack for a thumbs-up.** Each request gets its own Slack message. A rep or PM reacts
   with ✅ to approve it.
4. **Build it and push a branch.** Once approved, an AI coding agent implements the feature in the real
   product codebase, on its own branch, and pushes it to GitHub — ready for a human to review and for
   your existing CI to build a preview.

## The pieces (file map)

| File | What it does |
|---|---|
| `hubspot_client.py` | Looks up a company in HubSpot, by name or by email domain. |
| `resolver.py` | Ties a meeting to a customer's workspace, using HubSpot's answer. |
| `calendar_resolver.py` | The automatic version of the above — reads attendee emails straight off Vexa's own notification, no extra lookup needed. |
| `entity_files.py` | Reads the "feature request" notes the meeting agent wrote down. |
| `poller.py` | Checks for new feature requests and posts each one to Slack. |
| `store.py` | A small local database tracking which requests are pending, approved, or done. |
| `orchestrator.py` | Once approved: kicks off the AI coding turn, then checks in until it's done and pushes it. |
| `slack_client.py` / `slack_verify.py` | Talking to Slack, and proving a Slack request is really from Slack. |
| `webhook_verify.py` | Proving a Vexa notification is really from Vexa. |
| `hubspot_oauth.py` / `slack_oauth.py` | The "Connect HubSpot" / "Connect Slack" OAuth2 dance — authorize URL, code exchange, (HubSpot only) token refresh. |
| `oauth_routes.py` | The one shared "Connect X" route shape (authorize/callback/status/disconnect/paste-a-token) HubSpot and Slack both register through. |
| `api.py` | The web addresses (endpoints) everything above is reachable at. |
| `settings.py` | Every knob you can configure (API keys, URLs, etc.), in one place. |

## Where things stand

All five stages described above are built and covered by tests (100+ tests as of this writing).
Every credential below is empty by default (the stack runs fine with none of it set — the add-on
just won't have anything to look up yet); here's how to get each one for real.

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
  built via the steps above uses the classic `app.hubspot.com/oauth/authorize` endpoint and worked
  for us **without** needing PKCE — but HubSpot's platform has been changing fast enough this year
  that if "Connect HubSpot" ever starts failing with a PKCE-related error, that's the signal this
  has changed again, not that something here is misconfigured.

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
   doesn't hold the matching scope for).
3. **OAuth & Permissions** → **Redirect URLs** → add `http://localhost:18300/oauth/slack/callback`
   (this one's fine as `localhost` — it's your own browser that makes this request).
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
   checkmark within a couple seconds) → under **Subscribe to bot events** add `reaction_added` →
   save. **This step has no API — it can only be done here, by hand, once (and again each time an
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
   (`VEXA_GITHUB_OAUTH_REDIRECT_URI` / `VEXA_TERMINAL_URL` already default correctly for local dev.)

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

## The one small addition inside Vexa itself

Everything above talks to Vexa purely through its existing, public web API — except one thing: Vexa's
meeting-notes agent didn't have a way to know which customer's notes-folder it should write into. We
added one small switch inside Vexa (`core/agent/control_plane/transcription_watcher.py`,
`SALES_CYCLE_WORKSPACE_RESOLVE`, on by default) that reads "which customer is this meeting tagged as"
and uses that instead of writing every single call's notes into one shared folder. For any meeting
that isn't tagged, it falls straight through to the old behavior — we proved that by running Vexa's
own full test suite with the flag both on and off.

## Calendar auto-mapping: the webhook registers itself

The calendar path (candidate-domain matching against HubSpot) needs Vexa's `meeting.started`
webhook (docs/docs/webhooks.mdx) pointed at this add-on. Vexa itself has no settings UI for that —
it's normally a one-time `PUT /user/webhook` API call per account. The Terminal's "Connect
calendar" flow now does this automatically the first time a rep connects a calendar
(`clients/terminal/src/app/api/calendar/register-webhook/route.ts`), using
`SALES_CYCLE_CALENDAR_WEBHOOK_SECRET` as the shared secret. It never overwrites a webhook a rep
already has configured for something else — it only fills the setting in when it's empty or
already points here.
