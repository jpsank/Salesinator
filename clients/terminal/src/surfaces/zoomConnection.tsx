"use client";
/** Settings → Integrations → Zoom — "Connect Zoom". Unlike HubSpot/Slack next to it this is per-person: each rep
 *  connects their OWN Zoom account, and from then on Vexa's bot joins every meeting they start (scheduled or not).
 *  The relay is /api/zoom/* (app/api/zoom/[action]/route.ts); the add-on behind it is integrations/sales-cycle/.
 */
import { cardMeta, OAuthConnectionCard, type OAuthStatus } from "./integrationCard";
import { jsonOrThrow } from "./salesCycleApi";

interface LastJoin { topic?: string | null; zoom_meeting_id?: string | null; started_at: number; outcome: string; detail?: string | null }

export interface ZoomStatus extends OAuthStatus {
  /** False when the deployment has no webhook secret: Zoom could connect but could never say a meeting began. */
  webhook_ready?: boolean;
  last_join?: LastJoin | null;
}

export async function getZoomStatus(): Promise<ZoomStatus> {
  return jsonOrThrow(await fetch("/api/zoom/status", { cache: "no-store" }));
}

export async function disconnectZoom(): Promise<ZoomStatus> {
  return jsonOrThrow(await fetch("/api/zoom/disconnect", { method: "POST" }));
}

const OUTCOMES: Record<string, string> = {
  joined: "the bot joined",
  already_joined: "a bot was already there",
  limit_reached: "you're at your limit of concurrent bots",
  vexa_key_rejected: "Vexa rejected this connection's key — disconnect and reconnect Zoom",
  failed: "the bot couldn't join",
  pending: "joining…",
};

/** What the connection has actually done, so "Connected" is not the only evidence it works. */
export function ZoomActivity({ status }: { status: ZoomStatus }) {
  const last = status.last_join;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {status.webhook_ready === false && (
        <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>
          ⚠ Zoom can&rsquo;t tell Vexa when you start a meeting — this deployment has no webhook secret set
          (<code style={{ fontFamily: "var(--mono)" }}>SALES_CYCLE_ZOOM_WEBHOOK_SECRET_TOKEN</code>), so nothing will join.
        </div>
      )}
      <div style={cardMeta}>
        {last
          ? <>Last meeting: <b style={{ color: "var(--t2)" }}>{last.topic || last.zoom_meeting_id || "untitled"}</b> — {OUTCOMES[last.outcome] ?? last.outcome}
              {last.detail && last.outcome !== "joined" ? ` (${last.detail})` : ""}.</>
          : "No meeting yet — start one in Zoom and the bot joins within a few seconds."}
      </div>
    </div>
  );
}

export function ZoomCard() {
  return (
    <div style={{ maxWidth: 640 }}>
      <OAuthConnectionCard provider="zoom" label="Zoom"
        description="Vexa's bot joins every Zoom meeting you start — scheduled or not — and listens, the same as a call you add by hand. Only meetings you host; the bot is a visible participant."
        connectUrl="/api/zoom/authorize"
        getStatus={getZoomStatus} disconnect={disconnectZoom}
        extra={(status) => (
          <div style={{ borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
            <ZoomActivity status={status as ZoomStatus} />
          </div>
        )} />
    </div>
  );
}
