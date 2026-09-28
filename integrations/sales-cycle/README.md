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

All five stages described above are built and covered by tests (95 tests total as of this writing).
One piece needs a one-time setup step before it works for real: telling it where your actual
product codebase lives.

### One-time setup: the product repo

The "build it and push a branch" step (step 4) runs as one dedicated Vexa **subject** — just a
slug, e.g. `product-repo`, not a real login — whose own workspace is your actual product repo.
Attach it the exact same way any Vexa workspace attaches a custom git repo (the same mechanism
behind Settings → Workspaces → "attach a custom git repo" in the Terminal UI), just called
directly instead of through a browser session, since this subject has no login of its own:

```bash
curl -X POST "$AGENT_API_URL/api/workspace/swap" \
  -H "X-User-Id: product-repo" -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/your-org/your-product.git", "ref": "main", "token": "<a push-capable GitHub token>"}'
```

Then set `SALES_CYCLE_PRODUCT_REPO_SUBJECT=product-repo` (matching whatever subject you used
above). Nothing else is bespoke to sales-cycle here — it's the platform's own workspace-attach
primitive, reused for a non-human subject.

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
