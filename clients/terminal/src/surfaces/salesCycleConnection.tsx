"use client";
/** Settings → Integrations — "Connect HubSpot" / "Connect Slack" (integrations/sales-cycle/, a
 *  separate add-on outside Vexa's own codebase). These are SHARED, deployment-wide connections (one
 *  HubSpot account / one Slack workspace for the whole sales team) — not a per-person setting like
 *  Calendar or GitHub next to them — so there's no per-user identity in this flow at all.
 */
import { cardMeta, OAuthConnectionCard } from "./integrationCard";
import { disconnectOAuth, getOAuthStatus, oauthConnectUrl } from "./salesCycleApi";

export function SalesCycleSection() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 640 }}>
      <div style={{ ...cardMeta, maxWidth: 460 }}>
        One connection each, made once by whoever administers this deployment — not a per-rep setting.
      </div>
      <OAuthConnectionCard provider="hubspot" label="HubSpot"
        description="Lets meetings be tagged with the right customer by matching against your HubSpot companies — by name (a rep types it) or automatically by the attendee's email domain (calendar-synced calls)."
        connectUrl={oauthConnectUrl("hubspot")}
        getStatus={() => getOAuthStatus("hubspot")} disconnect={() => disconnectOAuth("hubspot")} />
      <OAuthConnectionCard provider="slack" label="Slack"
        description="Posts each captured feature request to a channel for a ✓ (approve) — and once GitHub is connected, an agent implements and pushes it."
        connectUrl={oauthConnectUrl("slack")}
        getStatus={() => getOAuthStatus("slack")} disconnect={() => disconnectOAuth("slack")} />
    </div>
  );
}
