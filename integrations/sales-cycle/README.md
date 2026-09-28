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
| `api.py` | The web addresses (endpoints) everything above is reachable at. |
| `settings.py` | Every knob you can configure (API keys, URLs, etc.), in one place. |

## Where things stand

All five stages described above are built and covered by tests (100+ tests as of this writing).
Every credential below is empty by default (the stack runs fine with none of it set — the add-on
just won't have anything to look up yet); here's how to get each one for real.

## One-time setup: connecting HubSpot, Slack, and GitHub

Every step below is also reachable interactively from Settings → Integrations in the Terminal
UI ("Connect HubSpot" / "Connect Slack" / the Product repo card) once the corresponding env vars
are set — this section is what to put IN those env vars, and the couple of steps ("Event
Subscriptions", the product repo's GitHub App) that can only be done on the provider's own site.

### HubSpot

Two ways to authenticate — pick one, don't set both:

- **Service Key** (recommended — HubSpot is retiring legacy private-app creation as of late
  September 2026): HubSpot account → **Settings → Integrations → Service Keys** (or
  **Development → Keys → Service keys**) → create one, name it, grant it the
  `crm.objects.companies.read` scope (a Service Key can only be granted scopes YOU already have).
  Copy the generated token into:
  ```
  SALES_CYCLE_HUBSPOT_TOKEN=pat-...
  ```
- **Private app token** (if your account still allows creating one): **Settings → Integrations →
  Private Apps → Create a private app** → under **Scopes** add `crm.objects.companies.read` →
  create → copy the token into the same `SALES_CYCLE_HUBSPOT_TOKEN`.
- **OAuth** (if you'd rather a rep click "Connect HubSpot" than paste a static token): create a
  **public app** at HubSpot's developer portal, set scope `crm.objects.companies.read`, set the
  redirect URL to `http://localhost:18300/oauth/hubspot/callback` (swap the host for your real
  domain if not running locally), then set:
  ```
  SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_ID=...
  SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_SECRET=...
  SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI=http://localhost:18300/oauth/hubspot/callback
  ```

### Slack

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
8. **Install to Workspace** (top of the app config) — required any time scopes change.
9. Set:
   ```
   SALES_CYCLE_SLACK_OAUTH_CLIENT_ID=...
   SALES_CYCLE_SLACK_OAUTH_CLIENT_SECRET=...
   SALES_CYCLE_SLACK_SIGNING_SECRET=...
   SALES_CYCLE_SLACK_CHANNEL_ID=...
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

**Preferred: the UI.** Once the GitHub OAuth App above is set up, open Settings → Integrations →
Sales Cycle → **Product repo** → **Connect GitHub** → pick your repo from the dropdown. No clone
URL typed, no token pasted — same "OAuth then pick from your own repos" flow as any Lovable-style
GitHub connect.

**Fallback: curl**, for a scripted/headless setup (attaches the repo the exact same way the UI's
"attach a custom git repo" flow does, just called directly since this subject has no login of its
own to click through):
```bash
curl -X POST "$AGENT_API_URL/api/workspace/swap" \
  -H "X-User-Id: product-repo" -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/your-org/your-product.git", "ref": "main", "token": "<a push-capable GitHub token>"}'
```

`SALES_CYCLE_PRODUCT_REPO_SUBJECT` already defaults to `product-repo` — only change it if you used
a different subject above.

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
