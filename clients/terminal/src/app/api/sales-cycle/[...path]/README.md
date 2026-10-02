# api/sales-cycle/[...path]

[`route.ts`](route.ts) — proxies `/api/sales-cycle/*` to the sales-cycle add-on's own backend
(`integrations/sales-cycle/`), mirroring the main `/api/[...path]` catch-all but targeting a
different host (`SALES_CYCLE_URL`, not the gateway) and without `X-API-Key`: the connections this
fronts (HubSpot, Slack, …) are shared/deployment-wide, not tied to whichever Vexa account is
logged in.

Access: only the paths the terminal's own client calls are forwarded (`oauth/<provider>/status|disconnect|token`,
`slack/channel`, `slack/channel-status`, `slack/approvers`) — anything else, including the backend's `/internal/*` and
`/dispatch`, is `404`. Reading (`GET`) needs a signed-in user (`401` otherwise); changing a connection
(any other method) needs an admin (`403` otherwise).
