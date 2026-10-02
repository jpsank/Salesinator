"use client";
/** salesCycleApi — the sales-cycle add-on's client (proxied through /api/sales-cycle/*, same
 *  jsonOrThrow convention as plannedApi.ts). This add-on lives outside Vexa's own codebase
 *  (integrations/sales-cycle/) — this file is its one seam into the Terminal's UI.
 *
 *  One generic shape per provider (HubSpot, Slack, ...) rather than a copy per provider — mirrors
 *  the backend's own oauth_routes.py, which registers the same four routes per provider.
 */
import { ApiError } from "./apiClient";
import type { OAuthStatus } from "./integrationCard";

export type { OAuthStatus };

export async function jsonOrThrow<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = "";
    try {
      const b = (await r.json()) as { detail?: unknown; error?: unknown };
      const d = b?.detail ?? b?.error;
      detail = typeof d === "string" ? d : d != null ? JSON.stringify(d).slice(0, 200) : "";
    } catch { /* body wasn't JSON — the status alone is the signal */ }
    throw new ApiError(r.status, detail, r.url);
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

export async function getOAuthStatus(provider: string): Promise<OAuthStatus> {
  return jsonOrThrow(await fetch(`/api/sales-cycle/oauth/${provider}/status`, { cache: "no-store" }));
}

export async function disconnectOAuth(provider: string): Promise<OAuthStatus> {
  return jsonOrThrow(await fetch(`/api/sales-cycle/oauth/${provider}/disconnect`, { method: "POST" }));
}

/** Paste a token directly instead of going through OAuth — e.g. HubSpot's Service Key / private-
 *  app token, for whoever's account can't or doesn't want to register an OAuth app. */
export async function setOAuthToken(provider: string, token: string): Promise<OAuthStatus> {
  return jsonOrThrow(await fetch(`/api/sales-cycle/oauth/${provider}/token`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }),
  }));
}

export interface SlackChannelStatus {
  configured: boolean;
  channel_id?: string | null;
  channel_name?: string | null;
  is_member?: boolean | null;
  error?: string | null;
}

/** Live-checks whether the connected Slack app can ACTUALLY post feature-request cards to the
 *  configured channel — not just whether OAuth succeeded. A connected-but-never-invited app looks
 *  identical to a working one from OAuth status alone. */
export async function getSlackChannelStatus(): Promise<SlackChannelStatus> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/channel-status", { cache: "no-store" }));
}

export interface SlackChannelConfig {
  channel_id?: string | null;
  /** "override" = set from this page; "env" = SALES_CYCLE_SLACK_CHANNEL_ID; "unset" = neither. */
  source: "override" | "env" | "unset";
}

/** The effective channel (a Settings-page override if one's been saved, else the deployment's env
 *  default) and where it came from — used to prefill the Channel ID field with what's ACTUALLY in
 *  effect, not a blank, and to label it as "from your env var" vs. "set here". */
export async function getSlackChannel(): Promise<SlackChannelConfig> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/channel", { cache: "no-store" }));
}

/** Sets the channel override — the UI alternative to editing SALES_CYCLE_SLACK_CHANNEL_ID and
 *  restarting the service. An empty string clears it, reverting to the env default. */
export async function setSlackChannel(channelId: string): Promise<SlackChannelConfig> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/channel", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ channel_id: channelId }),
  }));
}

export interface SlackApprovers {
  user_ids: string[];
  include_admins: boolean;
  usergroup_ids: string[];
  /** false = nobody chosen: the original rule applies (anyone's ✅ approves). */
  configured: boolean;
}

/** Who may give the go-ahead on a feature request (a leader's ✅ approves it once 👍 outnumber 👎). */
export async function getSlackApprovers(): Promise<SlackApprovers> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/approvers", { cache: "no-store" }));
}

/** Sets who may approve: Slack member ids (U…), workspace admins/owners, user group ids (S…) — any one makes a leader.
 *  Everything empty goes back to the original rule. The server validates the ids and answers 422 with what was wrong. */
export async function setSlackApprovers(a: Pick<SlackApprovers, "user_ids" | "include_admins" | "usergroup_ids">): Promise<SlackApprovers> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/approvers", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(a),
  }));
}

export interface SlackEventsStatus {
  /** Unix seconds of the last verified event Slack delivered, or null if none has ever arrived. */
  last_event_at: number | null;
  /** Unix seconds of the last request that said it was from Slack but failed its signature check. */
  last_rejected_at: number | null;
}

/** When Slack last sent the service an event — only a hint (a quiet channel and a broken connection look the same); the check settles it. */
export async function getSlackEventsStatus(): Promise<SlackEventsStatus> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/events-status", { cache: "no-store" }));
}

export interface SlackEventsCheck {
  delivered: boolean;
  /** Slack's request reached the service but its signature was refused (a wrong signing secret). */
  refused: boolean;
  target?: string | null;
  detail: string;
}

/** Tests end to end that Slack is delivering events: the bot reacts 👀 to the latest card, waits for the event, and takes it off (up to ~10 s). */
export async function checkSlackEvents(): Promise<SlackEventsCheck> {
  return jsonOrThrow(await fetch("/api/sales-cycle/slack/events-check", { method: "POST" }));
}

/** A "Connect X" link's target. A real cross-origin navigation (the browser leaves the Terminal
 *  entirely, via the sales-cycle service and then the provider's own consent screen, before
 *  landing back here) — not a fetch, so this is the one place in the sales-cycle integration that
 *  names the backend's own public URL rather than going through the /api/sales-cycle proxy. */
export function oauthConnectUrl(provider: string): string {
  const base = (process.env.NEXT_PUBLIC_SALES_CYCLE_URL || "http://localhost:18300").replace(/\/$/, "");
  return `${base}/oauth/${provider}/authorize`;
}
