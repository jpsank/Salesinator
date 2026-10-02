/** Vexa Capture's bot requests, relayed to the gateway — so the app talks to ONE public address (this site) instead of needing the
 *  gateway published as well.
 *
 *  This is deliberately not the catch-all /api proxy. That one authenticates as the signed-in browser user and falls back to the
 *  deployment's own key; here the caller is a native app with no cookie, so the identity is the app's own API key, taken ONLY from
 *  its X-API-Key header, and a request without one is refused rather than served as the deployment. The gateway resolves the key
 *  (and its scope and limits) exactly as for any API caller. Only the calls the app makes are relayed: request a bot, remove it, ask whether it is still running, and ask who its key is.
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const GATEWAY_URL = () => (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const MAX_BODY_BYTES = 16 * 1024;            // a bot request is a meeting link and a few options

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

async function relay(req: NextRequest, params: Promise<{ path: string[] }>, allowed: RegExp): Promise<Response> {
  const path = (await params).path.join("/");
  if (!allowed.test(path)) return json({ error: "not_found" }, 404);
  const key = (req.headers.get("x-api-key") || "").trim();
  if (!key) return json({ error: "An X-API-Key header is required." }, 401);

  const init: RequestInit = { method: req.method, headers: { "X-API-Key": key }, cache: "no-store", signal: AbortSignal.timeout(20000) };
  if (req.method === "POST") {
    const body = await req.text();
    if (new TextEncoder().encode(body).length > MAX_BODY_BYTES) return json({ error: "request too large" }, 413);
    init.body = body;
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  try {
    const upstream = await fetch(`${GATEWAY_URL()}/${path}`, init);
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("Content-Type") || "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return json({ error: "upstream_unavailable" }, 502);
  }
}

export function POST(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return relay(req, ctx.params, /^bots$/);
}

/** `GET auth/me` — the app's setup check asking "does Vexa accept my key, and as whom?"; `GET bots/status` — whether its bot is still running. */
export function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return relay(req, ctx.params, /^(auth\/me|bots\/status)$/);
}

export function DELETE(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return relay(req, ctx.params, /^bots\/[a-z_]{1,32}\/[^/]{1,255}$/);
}
