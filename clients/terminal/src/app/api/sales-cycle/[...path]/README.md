# api/sales-cycle/[...path]

[`route.ts`](route.ts) — proxies `/api/sales-cycle/*` to the sales-cycle add-on's own backend
(`integrations/sales-cycle/`), mirroring the main `/api/[...path]` catch-all but targeting a
different host (`SALES_CYCLE_URL`, not the gateway) and without `X-API-Key`: the connections this
fronts (HubSpot, Slack, …) are shared/deployment-wide, not tied to whichever Vexa account is
logged in.
