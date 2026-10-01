"use client";
/** Settings → Integrations — "Connect HubSpot" / "Connect Slack" (integrations/sales-cycle/, a
 *  separate add-on outside Vexa's own codebase). These are SHARED, deployment-wide connections (one
 *  HubSpot account / one Slack workspace for the whole sales team) — not a per-person setting like
 *  Calendar or GitHub next to them — so there's no per-user identity in this flow at all.
 */
import { useEffect, useState } from "react";
import { cardBtn, cardField, cardLabelCol, cardLabelled, cardMeta, OAuthConnectionCard, PasteTokenFallback } from "./integrationCard";
import {
  disconnectOAuth, getOAuthStatus, getSlackChannel, getSlackChannelStatus, oauthConnectUrl, setOAuthToken,
  setSlackChannel, type SlackChannelConfig, type SlackChannelStatus,
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
    return <div style={{ fontSize: 11.5, color: "var(--t3)" }}>No channel configured yet — set one above.</div>;
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

/** The channel ID field itself — what used to be ONLY `SALES_CYCLE_SLACK_CHANNEL_ID` (an env var +
 *  a restart) is now editable right here. Loads the EFFECTIVE value (a saved override if one
 *  exists, else the env default) so an untouched field shows what's actually in effect, not a
 *  blank; an empty Save clears the override and reverts to the env default — same convention as
 *  every other Settings field on this page (ConfigForm in settings.tsx). */
export function SlackChannelField({ onSaved = () => undefined }: { onSaved?: () => void }) {
  const [value, setValue] = useState("");
  const [initial, setInitial] = useState("");
  const [source, setSource] = useState<SlackChannelConfig["source"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let on = true;
    getSlackChannel()
      .then((c) => {
        if (!on) return;
        const v = c.channel_id ?? "";
        setValue(v); setInitial(v); setSource(c.source);
      })
      .catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, []);

  const dirty = value !== initial;
  const save = async () => {
    setBusy(true); setErr(null); setSaved(false);
    try {
      const c = await setSlackChannel(value.trim());
      const v = c.channel_id ?? "";
      setValue(v); setInitial(v); setSource(c.source); setSaved(true);
      onSaved();
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <label style={cardLabelled}>
        <span style={cardLabelCol}>Channel ID</span>
        <input value={value} placeholder="C0123ABCDEF"
          onChange={(e) => { setSaved(false); setValue(e.target.value); }}
          style={{ ...cardField, flex: 1 }} />
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button disabled={busy || !dirty} onClick={() => void save()}
          style={{ ...cardBtn, opacity: busy || !dirty ? 0.5 : 1 }}>
          {busy ? "Saving…" : "Save"}
        </button>
        {saved && <span style={{ fontSize: 11.5, color: "var(--green)" }}>Saved</span>}
        {!dirty && !saved && source === "env" && value && (
          <span style={cardMeta}>from SALES_CYCLE_SLACK_CHANNEL_ID &mdash; saving overrides it</span>
        )}
      </div>
      {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
    </div>
  );
}

/** Field + live check together. `version` forces SlackChannelCheck to remount (its own effect has
 *  no deps, so a plain re-render wouldn't re-poll) after a save, so the ✓/⚠ verdict reflects the
 *  channel you just set instead of the one that was configured when the page loaded. */
function SlackChannelSettings() {
  const [version, setVersion] = useState(0);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <SlackChannelField onSaved={() => setVersion((v) => v + 1)} />
      <SlackChannelCheck key={version} />
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
            <SlackChannelSettings />
          </div>
        )} />
    </div>
  );
}
