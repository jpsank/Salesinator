"use client";
/** Settings → Integrations — "Connect HubSpot" / "Connect Slack" (integrations/sales-cycle/, a
 *  separate add-on outside Vexa's own codebase). These are SHARED, deployment-wide connections (one
 *  HubSpot account / one Slack workspace for the whole sales team) — not a per-person setting like
 *  Calendar or GitHub next to them — so there's no per-user identity in this flow at all.
 */
import { cardMeta, OAuthConnectionCard } from "./integrationCard";
import { disconnectOAuth, getOAuthStatus, oauthConnectUrl } from "./salesCycleApi";
import { ProductRepoCard } from "./productRepoConnection";

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
        getStatus={() => getOAuthStatus("slack")} disconnect={() => disconnectOAuth("slack")}
        fallback={() => (
          <div style={{ ...cardMeta, borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
            <b style={{ color: "var(--t2)" }}>One more one-time step, in Slack's own dashboard:</b> the ✓
            reaction only triggers a build if your Slack app has <b style={{ color: "var(--t2)" }}>Event
            Subscriptions</b> turned on — Slack has no API for this, so it can't be automated from here.
            At <code style={{ fontFamily: "var(--mono)" }}>api.slack.com/apps</code> → your app → Event
            Subscriptions: enable it, set the Request URL to your sales-cycle service&rsquo;s public
            address + <code style={{ fontFamily: "var(--mono)" }}>/slack/events</code>, then under
            &ldquo;Subscribe to bot events&rdquo; add <code style={{ fontFamily: "var(--mono)" }}>reaction_added</code> and save.
          </div>
        )} />
      <ProductRepoCard />
    </div>
  );
}
