# api/capture/relay/[...path]

[`route.ts`](route.ts) — lets Vexa Capture (the Mac app) request and remove Vexa's bot through THIS site, so a deployment needs one
public address. Only `POST bots`, `DELETE bots/{platform}/{id}` and `GET auth/me` are relayed, and only with the app's own bot-scoped key in
`X-API-Key` — never the terminal's cookie or its fallback deployment key — which the gateway checks. The audio side is the
`/capture/ingest` WebSocket relay in `server.mjs`.
