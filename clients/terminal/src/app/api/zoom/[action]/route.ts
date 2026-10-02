/** "Connect Zoom" — one rep's own Zoom account, so every call needs the signed-in user's identity, which only this
 *  server has verified (the sales-cycle add-on is reachable from the internet and cannot take a caller's word for
 *  who they are). Each call to it carries VEXA_INTERNAL_API_SECRET, and the user id is the one resolved from the
 *  session cookie — never anything the request supplies.
 *
 *  Connecting also mints the user a bot-scoped Vexa key (named `zoom-auto-join`, listed in their Tokens panel):
 *  it is what the add-on sends the bot with, so a Zoom-joined bot behaves exactly like one the user added by hand.
 *  Disconnecting revokes it. A previous, never-finished attempt's key is revoked before a new one is minted.
 */
import { NextResponse, type NextRequest } from "next/server";
import { listUserTokens, mintUserToken, revokeToken } from "../../auth/adminApi";
import { currentUser } from "../../tokens/currentUser";
import { registerMeetingStartedWebhook } from "../../calendar/register-webhook/registerWebhook";

export const dynamic = "force-dynamic";

const SALES_CYCLE_URL = () => (process.env.SALES_CYCLE_URL || "http://127.0.0.1:18300").replace(/\/$/, "");
const KEY_NAME = "zoom-auto-join";
const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: NO_STORE });
/** A relative Location resolves against whatever origin the browser used, so this works behind any proxy. */
const backToSettings = (query: string) =>
  new Response(null, { status: 302, headers: { Location: `/?settings=integrations&${query}`, ...NO_STORE } });

function addon(path: string, init: { method: string; body?: unknown } & { query?: Record<string, string> }): Promise<Response> {
  const q = init.query ? `?${new URLSearchParams(init.query).toString()}` : "";
  return fetch(`${SALES_CYCLE_URL()}${path}${q}`, {
    method: init.method,
    headers: { "Content-Type": "application/json", "X-Internal-Secret": process.env.VEXA_INTERNAL_API_SECRET || "" },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    cache: "no-store",
    signal: AbortSignal.timeout(10000),
  });
}

async function authorize(): Promise<Response> {
  const me = await currentUser();
  if (!me.ok) return backToSettings("zoom_error=not_signed_in");

  // Drop a previous attempt's key before minting another, so abandoned connects never pile up.
  const listed = await listUserTokens(me.userId);
  for (const t of listed.ok ? listed.data ?? [] : []) {
    if (t.name === KEY_NAME) await revokeToken(t.id);
  }
  const minted = await mintUserToken(me.userId, { scopes: ["bot"], name: KEY_NAME });
  if (!minted.ok || !minted.data?.token) return backToSettings("zoom_error=key_failed");

  let upstream: Response;
  try {
    upstream = await addon("/zoom/authorize-link", {
      method: "POST",
      body: { vexa_user_id: String(me.userId), vexa_token: minted.data.token, vexa_token_id: minted.data.id },
    });
  } catch {
    await revokeToken(minted.data.id);
    return backToSettings("zoom_error=unreachable");
  }
  const body = upstream.ok ? ((await upstream.json().catch(() => ({}))) as { url?: string }) : {};
  if (!body.url) {
    await revokeToken(minted.data.id);
    return backToSettings(`zoom_error=${upstream.status === 503 ? "not_configured" : "failed"}`);
  }
  // A Zoom-joined bot is captured like any other: its meeting.started webhook has to reach the add-on. Best-effort —
  // a rep who already has a webhook of their own keeps it, and nothing here may stop them reaching Zoom.
  await registerMeetingStartedWebhook().catch(() => undefined);
  return NextResponse.redirect(body.url);
}

async function status(): Promise<Response> {
  const me = await currentUser();
  if (!me.ok) return json({ error: me.error }, me.status);
  try {
    const upstream = await addon("/zoom/status", { method: "GET", query: { vexa_user_id: String(me.userId) } });
    return json(await upstream.json().catch(() => ({})), upstream.status);
  } catch {
    return json({ error: "sales-cycle unreachable" }, 502);
  }
}

async function disconnect(): Promise<Response> {
  const me = await currentUser();
  if (!me.ok) return json({ error: me.error }, me.status);
  try {
    const upstream = await addon("/zoom/disconnect", { method: "POST", body: { vexa_user_id: String(me.userId) } });
    if (!upstream.ok) return json({ error: "sales-cycle refused the disconnect" }, upstream.status);
    const { vexa_token_id } = (await upstream.json()) as { vexa_token_id?: string | null };
    // Only a key that really is this user's own is revoked — the id came from a store keyed by their user id,
    // but ownership is checked against their token list anyway, as /api/tokens/[id] does.
    if (vexa_token_id) {
      const listed = await listUserTokens(me.userId);
      if ((listed.ok ? listed.data ?? [] : []).some((t) => String(t.id) === String(vexa_token_id))) {
        await revokeToken(Number(vexa_token_id));
      }
    }
    return json({ connected: false });
  } catch {
    return json({ error: "sales-cycle unreachable" }, 502);
  }
}

export async function GET(_req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action === "authorize") return authorize();
  if (action === "status") return status();
  return json({ error: "not_found" }, 404);
}

export async function POST(_req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action === "disconnect") return disconnect();
  return json({ error: "not_found" }, 404);
}
