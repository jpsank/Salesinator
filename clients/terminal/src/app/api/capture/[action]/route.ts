/** Pairing for Vexa Capture (the Mac menu-bar app) — one click instead of minting a token and typing three addresses.
 *
 *  1. The signed-in user opens GET /api/capture/connect in a browser. It mints a one-time CODE bound to them and shows a
 *     page whose link opens the app: `vexacapture://connect?code=…&base=<this site>`.
 *  2. The app POSTs the code to /api/capture/exchange. The code is single-use and good for two minutes; only then is a
 *     bot-scoped Vexa key minted (so a code nobody redeems leaves nothing behind) and returned with the API and capture-
 *     ingest addresses the app should use — this site's own relays unless the deployment publishes the services itself.
 *
 *  The key travels only in the exchange response, never in a URL (browser history). The code is the sole secret on the link,
 *  and it names a user the server already authenticated.
 */
import { randomBytes } from "node:crypto";
import { NextResponse, type NextRequest } from "next/server";
import { listUserTokens, mintUserToken, revokeToken } from "../../auth/adminApi";
import { currentUser } from "../../tokens/currentUser";

export const dynamic = "force-dynamic";

const KEY_NAME = "vexa-capture (Mac)";
const KEEP_KEYS = 4;                 // one per device is expected; older ones are revoked so pairing never piles them up
const CODE_TTL_MS = 120_000;
const NO_STORE = { "Cache-Control": "no-store, no-cache, must-revalidate" } as const;

interface Pending { userId: string | number; email: string; expires: number }
const pending = new Map<string, Pending>();

const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const html = (body: string, status = 200) => new NextResponse(
  `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Vexa Capture</title>` +
  `<body style="font:16px system-ui;max-width:32rem;margin:15vh auto;padding:0 1rem;line-height:1.5">${body}</body>`,
  { status, headers: { "Content-Type": "text/html; charset=utf-8", ...NO_STORE } },
);
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: NO_STORE });

/** The address the user's browser used — behind a proxy the request's own URL is the internal one. */
function publicOrigin(req: NextRequest): string {
  const host = req.headers.get("x-forwarded-host") || req.headers.get("host") || req.nextUrl.host;
  const proto = req.headers.get("x-forwarded-proto") || (/^(localhost|127\.0\.0\.1)(:|$)/.test(host) ? "http" : "https");
  return `${proto.split(",")[0]}://${host.split(",")[0]}`;
}

/** Where the app should send the bot request and stream audio. By default both are THIS site — the REST relay at
 *  /api/capture/relay and the WebSocket relay at /capture/ingest (server.mjs) — so one public address is enough. A deployment that
 *  publishes the gateway and the capture service itself can point the app straight at them. */
function addresses(origin: string): { api: string; ingest: string } {
  return {
    api: process.env.VEXA_CAPTURE_API_URL || `${origin}/api/capture/relay`,
    ingest: process.env.VEXA_CAPTURE_INGEST_URL || `${origin.replace(/^http/, "ws")}/capture/ingest`,
  };
}

function sweep(now: number): void {
  for (const [code, p] of pending) if (p.expires <= now) pending.delete(code);
}

async function connect(req: NextRequest): Promise<Response> {
  const origin = publicOrigin(req);
  const me = await currentUser();
  if (!me.ok) {
    return html(`<h2>Sign in to Vexa first</h2><p>Open <a href="${esc(origin)}/">${esc(origin)}</a>, sign in, then choose <b>Connect to Vexa</b> in Vexa Capture again.</p>`, 401);
  }
  const now = Date.now();
  sweep(now);
  const code = randomBytes(24).toString("base64url");
  pending.set(code, { userId: me.userId, email: me.email, expires: now + CODE_TTL_MS });
  const link = `vexacapture://connect?code=${encodeURIComponent(code)}&base=${encodeURIComponent(origin)}`;
  return html(
    `<h2>Connecting Vexa Capture…</h2><p>Signed in as <b>${esc(me.email)}</b>. If Vexa Capture doesn't open, <a id="open" href="${esc(link)}">open it</a>.</p>` +
    `<script>location.href=${JSON.stringify(link)}</script>`,
  );
}

async function exchange(req: NextRequest): Promise<Response> {
  let code = "";
  try { code = String(((await req.json()) as { code?: unknown }).code ?? ""); } catch { /* falls through to the 404 */ }
  const now = Date.now();
  const p = pending.get(code);
  pending.delete(code);                                            // single use, whether or not it is still fresh
  sweep(now);
  if (!p || p.expires <= now) return json({ error: "That connection link has expired — choose Connect to Vexa again." }, 404);
  const addr = addresses(publicOrigin(req));

  const listed = await listUserTokens(p.userId);
  const mine = (listed.ok ? listed.data ?? [] : []).filter((t) => t.name === KEY_NAME).sort((a, b) => b.id - a.id);
  for (const old of mine.slice(KEEP_KEYS - 1)) await revokeToken(old.id);
  const minted = await mintUserToken(p.userId, { scopes: ["bot"], name: KEY_NAME });
  if (!minted.ok || !minted.data?.token) return json({ error: "Couldn't create a key for Vexa Capture." }, 502);
  return json({ key: minted.data.token, api: addr.api, ingest: addr.ingest, account: p.email });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  return action === "connect" ? connect(req) : json({ error: "not_found" }, 404);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  return action === "exchange" ? exchange(req) : json({ error: "not_found" }, 404);
}
