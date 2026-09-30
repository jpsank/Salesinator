"use client";
/** Settings → Integrations — "Connect HubSpot" / "Connect Slack" (integrations/sales-cycle/, a
 *  separate add-on outside Vexa's own codebase). These are SHARED, deployment-wide connections (one
 *  HubSpot account / one Slack workspace for the whole sales team) — not a per-person setting like
 *  Calendar or GitHub next to them — so there's no per-user identity in this flow at all.
 */
import { useEffect, useState } from "react";
import { cardMeta, OAuthConnectionCard, PasteTokenFallback } from "./integrationCard";
import {
  disconnectOAuth, getOAuthStatus, getSlackChannelStatus, oauthConnectUrl, setOAuthToken,
  type SlackChannelStatus,
} from "./salesCycleApi";
import { presentError } from "./apiClient";

/** Live-checks the connected Slack app against the configured channel — NOT just whether OAuth
 *  succeeded. Reproduced live: a real feature_request card was tagged from a real call and never
 *  reached Slack, because the app had never been invited into the channel — OAuth alone can't tell
 *  you that, since a connected-but-uninvited app looks identical to a working one. This surfaces the
 *  failure at SETUP time (right here, on this page) instead of after a card is already lost. */
export function SlackChannelCheck() {
  const [status, setStatus] = useState<SlackChannelStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let on = true;
    getSlackChannelStatus()
      .then((s) => on && setStatus(s))
      .catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, []);

  if (err) return <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ Couldn&rsquo;t check the channel: {err}</div>;
  if (status === null) return <div style={cardMeta}>Checking the configured channel…</div>;
  if (!status.configured) {
    return (
      <div style={{ fontSize: 11.5, color: "var(--t3)" }}>
        No channel configured yet — set <code style={{ fontFamily: "var(--mono)" }}>SALES_CYCLE_SLACK_CHANNEL_ID</code>.
      </div>
    );
  }
  if (status.error) {
    const reason = status.error === "channel_not_found"
      ? "that channel ID doesn't exist, or it's private and the app was never invited"
      : status.error === "invalid_auth" || status.error === "token_revoked" || status.error === "account_inactive"
        ? "the Slack connection is invalid — reconnect Slack"
        : `Slack rejected the check (${status.error})`;
    return (
      <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>
        ⚠ Channel <code style={{ fontFamily: "var(--mono)" }}>{status.channel_id}</code> isn&rsquo;t usable — {reason}.
      </div>
    );
  }
  if (status.is_member === false) {
    return (
      <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>
        ⚠ Connected, but not invited into <code style={{ fontFamily: "var(--mono)" }}>#{status.channel_name ?? status.channel_id}</code> —
        in Slack, open that channel and run <code style={{ fontFamily: "var(--mono)" }}>/invite @&lt;this app&gt;</code>.
      </div>
    );
  }
  return (
    <div style={{ fontSize: 11.5, color: "var(--green)" }}>
      ✓ Ready — posting to <code style={{ fontFamily: "var(--mono)" }}>#{status.channel_name ?? status.channel_id}</code>.
    </div>
  );
}

export function SalesCycleSection() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 640 }}>
      <div style={{ ...cardMeta, maxWidth: 460 }}>
        One connection each, made once by whoever administers this deployment — not a per-rep setting.
      </div>
      <OAuthConnectionCard provider="hubspot" label="HubSpot"
        description="Lets meetings be tagged with the right customer by matching against your HubSpot companies — by name (a rep types it) or automatically by the attendee's email domain (calendar-synced calls)."
        connectUrl={oauthConnectUrl("hubspot")}
        getStatus={() => getOAuthStatus("hubspot")} disconnect={() => disconnectOAuth("hubspot")}
        fallback={(onConnected) => (
          <PasteTokenFallback
            description="Or paste a Service Key / private-app token (HubSpot is retiring OAuth-free private apps — a Service Key is the current replacement):"
            placeholder="pat-…" saveToken={(token) => setOAuthToken("hubspot", token)}
            onConnected={onConnected} />
        )} />
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
        )}
        extra={() => (
          <div style={{ borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
            <SlackChannelCheck />
          </div>
        )} />
    </div>
  );
}
