/** Point Vexa's own `meeting.started` webhook (docs/docs/webhooks.mdx) at the sales-cycle add-on, for the
 *  signed-in user — shared by the calendar-connect flow (`route.ts`) and "Connect Zoom"
 *  (app/api/zoom/[action]/route.ts): either way the bot then joins calls whose capture the add-on must react to.
 *  Best-effort and never overwrites a `webhook_url` the account already has configured for something else.
 */
import { resolveApiKey } from "../../proxyAuth";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const SALES_CYCLE_URL = (process.env.SALES_CYCLE_URL || "").replace(/\/$/, "");
const WEBHOOK_SECRET = process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET || "";

interface UserWebhook {
  webhook_url?: string;
  webhook_events?: Record<string, boolean>;
}

export interface RegisterResult { body: Record<string, unknown>; status: number }

export async function registerMeetingStartedWebhook(): Promise<RegisterResult> {
  if (!SALES_CYCLE_URL || !WEBHOOK_SECRET) {
    // Not an error — most Vexa deployments don't run the sales-cycle add-on at all.
    return { body: { registered: false, notConfigured: true }, status: 200 };
  }
  const targetUrl = `${SALES_CYCLE_URL}/webhooks/meeting-started`;
  const headers = { "X-API-Key": await resolveApiKey() };

  let current: UserWebhook;
  try {
    const got = await fetch(`${GATEWAY_URL}/user/webhook`, { headers, cache: "no-store" });
    current = got.ok ? ((await got.json()) as UserWebhook) : {};
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return { body: { registered: false, reason: `couldn't read the current webhook (${detail})` }, status: 502 };
  }

  if (current.webhook_url && current.webhook_url !== targetUrl) {
    // A different webhook is already configured for this account — that's this account's own
    // integration, never ours to silently replace.
    return { body: { registered: false, reason: "a different webhook is already configured for this account" }, status: 200 };
  }
  if (current.webhook_url === targetUrl && current.webhook_events?.["meeting.started"]) {
    return { body: { registered: true, already: true }, status: 200 };
  }

  try {
    const put = await fetch(`${GATEWAY_URL}/user/webhook`, {
      method: "PUT", headers: { ...headers, "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({
        webhook_url: targetUrl, webhook_secret: WEBHOOK_SECRET,
        webhook_events: { ...current.webhook_events, "meeting.started": true },
      }),
    });
    if (!put.ok) return { body: { registered: false, reason: `PUT /user/webhook → ${put.status}` }, status: 502 };
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return { body: { registered: false, reason: `couldn't reach Vexa to register the webhook (${detail})` }, status: 502 };
  }
  return { body: { registered: true }, status: 200 };
}
