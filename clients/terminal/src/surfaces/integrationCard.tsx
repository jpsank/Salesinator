"use client";
/** Shared visual language + machinery for every "connect an external service" card in
 *  Settings → Integrations (Calendar, GitHub, HubSpot, Slack). These used to each define their own
 *  near-identical copy of the style constants and the connect/disconnect card shape
 *  (calendarConnections.tsx, tokens.tsx, salesCycleConnection.tsx) — independently, so small
 *  differences crept in (padding, font-size, spacing). One definition now; every card imports these
 *  instead of rolling its own.
 */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { presentError } from "./apiClient";

export const cardField: CSSProperties = { width: "100%", minWidth: 0, boxSizing: "border-box", fontSize: 12, padding: "6px 9px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)" };
/** A field inside a `cardLabelled` row: takes the remaining width, wraps under its label when it would drop below 180px. */
export const cardFieldGrow: CSSProperties = { ...cardField, flex: "1 1 180px" };
export const cardBtn: CSSProperties = { fontSize: 12, padding: "5px 12px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: "pointer" };
export const cardPrimaryBtn: CSSProperties = { ...cardBtn, background: "var(--accent)", color: "var(--on-accent)", border: "none" };
export const cardRow: CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 8 };
export const cardMeta: CSSProperties = { fontSize: 11, color: "var(--t3)", lineHeight: 1.5 };
export const cardLabelled: CSSProperties = { display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, fontSize: 12, color: "var(--t2)" };
export const cardLabelCol: CSSProperties = { width: 96, flex: "none", color: "var(--t3)" };
export const cardCheckRow: CSSProperties = { display: "flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--t2)", cursor: "pointer" };

export interface OAuthStatus {
  connected: boolean;
  account_label?: string | null;
  /** Whether this deployment even has the provider's OAuth app registered. Missing/undefined is
   *  treated as configured (older backends that predate this field never had a reason to say no). */
  configured?: boolean;
}

/** The one-time banner from an OAuth redirect landing back on this page
 *  (?{provider}_connected=1 / ?{provider}_error=...) — read once, then dropped from the URL; a
 *  refresh shows the real polled status instead of a flag stuck in the address bar. */
export function useOAuthRedirectFeedback(provider: string): { connected: boolean; error: string | null } {
  const [state] = useState(() => {
    if (typeof window === "undefined") return { connected: false, error: null };
    const params = new URLSearchParams(window.location.search);
    const connected = params.get(`${provider}_connected`) === "1";
    const error = params.get(`${provider}_error`);
    if (connected || error) {
      params.delete(`${provider}_connected`); params.delete(`${provider}_error`);
      const q = params.toString();
      window.history.replaceState(null, "", window.location.pathname + (q ? `?${q}` : ""));
    }
    return { connected, error };
  });
  return state;
}

/** The "paste a token directly" fallback every OAuth-style card can offer alongside its Connect
 *  button — GitHub's own PAT paste, HubSpot's Service Key/private-app token, etc. One definition
 *  instead of a near-identical copy per provider. */
export function PasteTokenFallback({ description, placeholder, saveToken, onConnected }: {
  description: string;
  placeholder: string;
  saveToken: (token: string) => Promise<OAuthStatus>;
  onConnected: (status: OAuthStatus) => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!value.trim() || busy) return;
    setBusy(true); setError(null);
    try { onConnected(await saveToken(value.trim())); setValue(""); }
    catch (e: unknown) { setError(presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
      <div style={cardMeta}>{description}</div>
      <div style={{ display: "flex", gap: 8 }}>
        <input type="password" value={value} placeholder={placeholder} disabled={busy}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void save(); }} style={{ ...cardField, flex: 1 }} />
        <button disabled={busy || !value.trim()} onClick={() => void save()}
          style={{ ...cardBtn, opacity: busy || !value.trim() ? 0.5 : 1 }}>{busy ? "Saving…" : "Save"}</button>
      </div>
      {error && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {error}</div>}
    </div>
  );
}

/** The one card shape every OAuth-style connection uses: a status line, a Connect (link, real
 *  navigation) or Disconnect button, a description, and the redirect-feedback banner. `fallback` is
 *  an optional extra control shown only while NOT connected — GitHub's paste-a-PAT option, so OAuth
 *  is the default path but never the only one. `extra` is the connected-only counterpart — a
 *  next-step control that only makes sense once this connection exists, e.g. GitHub's product-repo
 *  picker (folded into the same card instead of a second, near-identical "Connect GitHub"). */
export function OAuthConnectionCard({
  provider, label, description, connectUrl, getStatus, disconnect, fallback, extra,
}: {
  provider: string;
  label: string;
  description: string;
  connectUrl: string;
  getStatus: () => Promise<OAuthStatus>;
  disconnect: () => Promise<OAuthStatus>;
  /** Rendered only while NOT connected — e.g. GitHub's paste-a-PAT option, so OAuth is the default
   *  path but never the only one. Call `onConnected` with the fresh status once the fallback path
   *  succeeds, so the card updates immediately instead of waiting on its own next status poll. */
  fallback?: (onConnected: (status: OAuthStatus) => void) => ReactNode;
  /** Rendered only while CONNECTED — a step that needs this connection to exist first. */
  extra?: (status: OAuthStatus) => ReactNode;
}) {
  const [status, setStatus] = useState<OAuthStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const redirectFeedback = useOAuthRedirectFeedback(provider);
  const mountedRef = useRef(true);
  useEffect(() => () => { mountedRef.current = false; }, []);

  // A transient failure (GitHub rate-limited, a network blip, agent-api mid-restart) used to leave
  // this card stuck on its error banner forever — the status check ran once on mount and never
  // again, so the only way out was a full page reload. Reproduced live: the gateway's own logs
  // showed a 502 burst, then NOTHING for the next 28 hours, while the card kept showing the same
  // stale error the whole time. `refresh` is reusable so the Retry button below re-runs the exact
  // same check instead of needing a reload.
  const refresh = useCallback(() => {
    setChecking(true); setErr(null);
    return getStatus()
      .then((s) => { if (mountedRef.current) setStatus(s); })
      .catch((e: unknown) => { if (mountedRef.current) setErr(presentError(e).headline); })
      .finally(() => { if (mountedRef.current) setChecking(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider]);

  useEffect(() => { void refresh(); }, [refresh]);

  const doDisconnect = async () => {
    setBusy(true); setErr(null);
    try { setStatus(await disconnect()); }
    catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  return (
    <div style={cardRow}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--t1)" }}>{label}</span>
        <span style={{ flex: 1, fontSize: 11.5, color: "var(--t3)" }}>
          {status === null ? (checking ? "Checking…" : "")
            : status.connected ? `Connected${status.account_label ? ` · ${status.account_label}` : ""}`
            : "Not connected"}
        </span>
        {/* status === null + err: the check failed — true state unknown, so neither Connect nor
            Disconnect is shown (either would be a guess); the Retry button below is the only action. */}
        {status?.connected ? (
          <button disabled={busy} onClick={() => void doDisconnect()} style={{ ...cardBtn, color: "var(--danger)" }}>
            {busy ? "Disconnecting…" : "Disconnect"}
          </button>
        ) : status?.configured === false ? (
          <span style={{ fontSize: 11.5, color: "var(--t3)" }}>OAuth not registered on this deployment</span>
        ) : status !== null ? (
          <a href={connectUrl} style={{ ...cardPrimaryBtn, textDecoration: "none", display: "inline-block" }}>
            Connect
          </a>
        ) : null}
      </div>
      <div style={cardMeta}>{description}</div>
      {status !== null && !status.connected && fallback?.(setStatus)}
      {status?.connected && extra?.(status)}
      {redirectFeedback.connected && (
        <div role="status" style={{ fontSize: 11.5, color: "var(--green)" }}>✓ {label} connected.</div>
      )}
      {redirectFeedback.error && (
        <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ Connecting {label} failed: {redirectFeedback.error}</div>
      )}
      {err && (
        <div role="alert" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--danger)" }}>
          <span style={{ flex: 1 }}>⚠ {err}</span>
          <button disabled={checking} onClick={() => void refresh()} style={cardBtn}>{checking ? "Retrying…" : "Retry"}</button>
        </div>
      )}
    </div>
  );
}
