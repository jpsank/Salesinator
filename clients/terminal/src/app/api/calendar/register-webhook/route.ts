/** Best-effort: point Vexa's own `meeting.started` webhook (docs/docs/webhooks.mdx) at the
 *  sales-cycle add-on, so its customer-mapping/feature-request pipeline reacts as soon as a
 *  calendar-synced call starts — instead of requiring a rep to hand-run `PUT /user/webhook`
 *  themselves (Vexa's own webhook docs: "No settings UI — configuration is API-only"). Called
 *  from the calendar-connect flow (surfaces/calendarConnections.tsx) after a calendar is added.
 *
 *  Never overwrites a `webhook_url` the account already has configured for something else of its
 *  own — this only fills the setting in when it's empty or already points at us.
 */
import { resolveApiKey } from "../../proxyAuth";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const SALES_CYCLE_URL = (process.env.SALES_CYCLE_URL || "").replace(/\/$/, "");
const WEBHOOK_SECRET = process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET || "";

interface UserWebhook {
  webhook_url?: string;
  webhook_events?: Record<string, boolean>;
}

export async function POST() {
  if (!SALES_CYCLE_URL || !WEBHOOK_SECRET) {
    // Not an error — most Vexa deployments don't run the sales-cycle add-on at all.
    return Response.json({ registered: false, notConfigured: true });
  }
  const targetUrl = `${SALES_CYCLE_URL}/webhooks/meeting-started`;
  const headers = { "X-API-Key": await resolveApiKey() };

  let current: UserWebhook;
  try {
    const got = await fetch(`${GATEWAY_URL}/user/webhook`, { headers, cache: "no-store" });
    current = got.ok ? ((await got.json()) as UserWebhook) : {};
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return Response.json({ registered: false, reason: `couldn't read the current webhook (${detail})` }, { status: 502 });
  }

  if (current.webhook_url && current.webhook_url !== targetUrl) {
    // A different webhook is already configured for this account — that's this account's own
    // integration, never ours to silently replace.
    return Response.json({ registered: false, reason: "a different webhook is already configured for this account" });
  }
  if (current.webhook_url === targetUrl && current.webhook_events?.["meeting.started"]) {
    return Response.json({ registered: true, already: true });
  }

  try {
    const put = await fetch(`${GATEWAY_URL}/user/webhook`, {
      method: "PUT", headers: { ...headers, "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({
        webhook_url: targetUrl, webhook_secret: WEBHOOK_SECRET,
        webhook_events: { ...current.webhook_events, "meeting.started": true },
      }),
    });
    if (!put.ok) return Response.json({ registered: false, reason: `PUT /user/webhook → ${put.status}` }, { status: 502 });
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return Response.json({ registered: false, reason: `couldn't reach Vexa to register the webhook (${detail})` }, { status: 502 });
  }
  return Response.json({ registered: true });
}
