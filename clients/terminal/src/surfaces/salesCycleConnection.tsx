"use client";
/** Settings → Integrations — "Connect HubSpot" / "Connect Slack" (integrations/sales-cycle/, a
 *  separate add-on outside Vexa's own codebase). These are SHARED, deployment-wide connections (one
 *  HubSpot account / one Slack workspace for the whole sales team) — not a per-person setting like
 *  Calendar or GitHub next to them — so there's no per-user identity in this flow at all.
 */
import { useEffect, useState } from "react";
import { cardBtn, cardFieldGrow, cardLabelCol, cardLabelled, cardMeta, OAuthConnectionCard, PasteTokenFallback } from "./integrationCard";
import {
  checkSlackEvents, disconnectOAuth, getOAuthStatus, getSlackApprovers, getSlackChannel, getSlackChannelStatus, getSlackEventsStatus,
  oauthConnectUrl, setOAuthToken, setSlackApprovers, setSlackChannel,
  type SlackApprovers, type SlackChannelConfig, type SlackChannelStatus, type SlackEventsCheck, type SlackEventsStatus,
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
          style={cardFieldGrow} />
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

/** Slack ids typed into a box: commas, spaces or new lines between them. */
const splitIds = (s: string): string[] => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

/** Who may give the go-ahead on a feature request. The team votes with 👍/👎 on the card and a leader's ✅ approves it once 👍
 *  outnumber 👎; a leader is anyone who matches ANY of: the member ids listed here, a workspace admin/owner (when ticked), or a
 *  member of a listed user group. Nothing chosen = the original rule, anyone's ✅ — and the card says so. */
export function SlackApproversField() {
  const [users, setUsers] = useState("");
  const [groups, setGroups] = useState("");
  const [admins, setAdmins] = useState(false);
  const [initial, setInitial] = useState<SlackApprovers | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const apply = (a: SlackApprovers) => {
    setUsers(a.user_ids.join(", ")); setGroups(a.usergroup_ids.join(", ")); setAdmins(a.include_admins); setInitial(a);
  };
  useEffect(() => {
    let on = true;
    getSlackApprovers().then((a) => on && apply(a)).catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, []);

  const dirty = initial !== null && (
    splitIds(users).join(",") !== initial.user_ids.join(",") || splitIds(groups).join(",") !== initial.usergroup_ids.join(",") || admins !== initial.include_admins);
  const save = async () => {
    setBusy(true); setErr(null); setSaved(false);
    try {
      apply(await setSlackApprovers({ user_ids: splitIds(users), include_admins: admins, usergroup_ids: splitIds(groups) }));
      setSaved(true);
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--t2)" }}>Who can approve</div>
      {initial && (initial.configured
        ? <div style={{ fontSize: 11.5, color: "var(--green)" }}>✓ The team votes with 👍/👎; a leader&rsquo;s ✅ approves once 👍 outnumber 👎.</div>
        : <div role="status" style={{ fontSize: 11.5, color: "var(--warn, #b45309)" }}>⚠ Nobody chosen yet — anyone in the channel can approve with a ✅. Choose who can below.</div>)}
      <label style={cardLabelled}>
        <span style={cardLabelCol}>Leaders (member IDs)</span>
        <input value={users} placeholder="U0123ABCD, U0456EFGH"
          onChange={(e) => { setSaved(false); setUsers(e.target.value); }} style={cardFieldGrow} />
      </label>
      <label style={{ ...cardLabelled, alignItems: "center" }}>
        <span style={cardLabelCol}>Workspace admins</span>
        <input type="checkbox" checked={admins} aria-label="Workspace admins and owners can approve"
          onChange={(e) => { setSaved(false); setAdmins(e.target.checked); }} />
        <span style={cardMeta}>admins and owners of the Slack workspace</span>
      </label>
      <label style={cardLabelled}>
        <span style={cardLabelCol}>User groups (IDs)</span>
        <input value={groups} placeholder="S0123ABCD"
          onChange={(e) => { setSaved(false); setGroups(e.target.value); }} style={cardFieldGrow} />
      </label>
      <div style={cardMeta}>Any one of these makes someone a leader. Find a member ID in Slack: profile &rarr; &#8942; &rarr; Copy member ID.</div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button disabled={busy || !dirty} onClick={() => void save()} style={{ ...cardBtn, opacity: busy || !dirty ? 0.5 : 1 }}>
          {busy ? "Saving…" : "Save"}
        </button>
        {saved && <span style={{ fontSize: 11.5, color: "var(--green)" }}>Saved</span>}
      </div>
      {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
    </div>
  );
}

/** "3 min ago", from unix seconds. */
export function agoFrom(unixSeconds: number, nowMs: number): string {
  const s = Math.max(0, Math.round(nowMs / 1000 - unixSeconds));
  if (s < 10) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

/** Is Slack actually sending events? Nothing else in the product says — reactions just do nothing when it is not (Socket Mode switched on
 *  in the Slack app, a wrong Request URL, a subscription Slack paused). The last event is only a hint — a quiet channel and a broken
 *  connection look the same — so "Check" proves it end to end: the bot reacts to the latest card and waits for Slack to report it. */
export function SlackEventsHealth({ now = () => Date.now() }: { now?: () => number }) {
  const [status, setStatus] = useState<SlackEventsStatus | null>(null);
  const [result, setResult] = useState<SlackEventsCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let on = true;
    getSlackEventsStatus().then((s) => on && setStatus(s)).catch(() => undefined);
    return () => { on = false; };
  }, []);

  const check = async () => {
    setBusy(true); setErr(null); setResult(null);
    try {
      setResult(await checkSlackEvents());
      setStatus(await getSlackEventsStatus());
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--t2)" }}>Slack events</div>
      <div style={cardMeta}>
        {status === null ? "…"
          : status.last_event_at === null ? "No event has reached Vexa yet."
          : `Last event from Slack: ${agoFrom(status.last_event_at, now())}.`}
        {status?.last_rejected_at != null && ` A request from Slack was refused ${agoFrom(status.last_rejected_at, now())}.`}
        {" "}Reactions (votes, ✅) only work while Slack is sending these.
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button disabled={busy} onClick={() => void check()} style={{ ...cardBtn, opacity: busy ? 0.5 : 1 }}>
          {busy ? "Checking (up to 10 s)…" : "Check that events arrive"}
        </button>
      </div>
      {result && (
        <div role={result.delivered ? "status" : "alert"}
          style={{ fontSize: 11.5, color: result.delivered ? "var(--green)" : "var(--danger)", overflowWrap: "anywhere" }}>
          {result.delivered ? "✓ " : "⚠ "}{result.detail}
        </div>
      )}
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
            &ldquo;Subscribe to bot events&rdquo; add <code style={{ fontFamily: "var(--mono)" }}>reaction_added</code> and{" "}
            <code style={{ fontFamily: "var(--mono)" }}>reaction_removed</code>, and save. To approve by vote, the app also needs the{" "}
            <code style={{ fontFamily: "var(--mono)" }}>reactions:write</code> scope (and <code style={{ fontFamily: "var(--mono)" }}>users:read</code>{" "}
            / <code style={{ fontFamily: "var(--mono)" }}>usergroups:read</code> for admins or a user group) — then reconnect Slack.
          </div>
        )}
        extra={() => (
          <div style={{ borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
            <SlackChannelSettings />
            <div style={{ borderTop: "1px dashed var(--line)", marginTop: 10, paddingTop: 10 }}>
              <SlackApproversField />
            </div>
            <div style={{ borderTop: "1px dashed var(--line)", marginTop: 10, paddingTop: 10 }}>
              <SlackEventsHealth />
            </div>
          </div>
        )} />
    </div>
  );
}
