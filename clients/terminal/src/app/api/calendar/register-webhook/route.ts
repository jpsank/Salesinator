/** Best-effort: point Vexa's own `meeting.started` webhook (docs/docs/webhooks.mdx) at the
 *  sales-cycle add-on, so its customer-mapping/feature-request pipeline reacts as soon as a
 *  calendar-synced call starts — instead of requiring a rep to hand-run `PUT /user/webhook`
 *  themselves (Vexa's own webhook docs: "No settings UI — configuration is API-only"). Called
 *  from the calendar-connect flow (surfaces/calendarConnections.tsx) after a calendar is added.
 *
 *  Never overwrites a `webhook_url` the account already has configured for something else of its
 *  own — this only fills the setting in when it's empty or already points at us.
 */
import { registerMeetingStartedWebhook } from "./registerWebhook";

export const dynamic = "force-dynamic";

export async function POST() {
  const { body, status } = await registerMeetingStartedWebhook();
  return Response.json(body, { status });
}
