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

async function jsonOrThrow<T>(r: Response): Promise<T> {
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

/** A "Connect X" link's target. A real cross-origin navigation (the browser leaves the Terminal
 *  entirely, via the sales-cycle service and then the provider's own consent screen, before
 *  landing back here) — not a fetch, so this is the one place in the sales-cycle integration that
 *  names the backend's own public URL rather than going through the /api/sales-cycle proxy. */
export function oauthConnectUrl(provider: string): string {
  const base = (process.env.NEXT_PUBLIC_SALES_CYCLE_URL || "http://localhost:18300").replace(/\/$/, "");
  return `${base}/oauth/${provider}/authorize`;
}
