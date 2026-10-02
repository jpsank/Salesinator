- **Connect Zoom: each rep connects their own Zoom account so the bot can join the meetings they host, scheduled or not.**
  A Zoom card in **Settings → Integrations** runs the OAuth consent; Zoom's signed `meeting.started` notification then sends
  the bot with a bot-scoped key minted for that rep. Needs a Zoom app registered by whoever runs the deployment
  (`python -m sales_cycle.zoom_check` confirms the settings, the redirect address and the public webhook handshake). The
  signature, handshake and join are covered by tests; Zoom's own payload has not yet been seen from a real app.
- **The sales-cycle add-on's public address now serves only what has to be public.** Everything but the OAuth callbacks, Slack
  and Zoom webhooks, `/tag`, `/dispatch` and `/health` requires the deployment's internal secret, which the Terminal's proxy
  and the cron job present.
