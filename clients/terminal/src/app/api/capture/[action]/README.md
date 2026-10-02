# api/capture/[action]

[`route.ts`](route.ts) — `GET connect` (the signed-in user's browser: mints a one-time code and hands it to the app through a
`vexacapture://connect?code=…&base=<this origin>` link) and `GET status` / `POST pair` / `POST revoke` (Settings → Integrations: the Macs paired, start pairing from the card, disconnect one) and `POST exchange` (the app: trades the code, once, within two minutes,
for a bot-scoped key named `vexa-capture (Mac)` plus the public API and capture-ingest addresses). The key never appears in a URL,
and the user is always the one resolved from the session cookie.
