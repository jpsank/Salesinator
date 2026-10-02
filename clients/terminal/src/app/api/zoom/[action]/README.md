# api/zoom/[action]

[`route.ts`](route.ts) — the signed-in user's own "Connect Zoom" connection, relayed to the sales-cycle add-on:
`GET authorize` (mints a bot-scoped key, then sends the browser to Zoom's consent screen), `GET status`, and
`POST disconnect` (forgets the connection and revokes that key). The add-on is reachable from the internet, so
every call to it carries `VEXA_INTERNAL_API_SECRET`; the user id always comes from the validated session cookie,
never from the request.
