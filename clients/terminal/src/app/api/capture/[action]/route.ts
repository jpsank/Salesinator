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

/** A one-time code for this user and the `vexacapture://` link that carries it to the app. */
function newLink(me: { userId: string | number; email: string }, origin: string): string {
  const now = Date.now();
  sweep(now);
  const code = randomBytes(24).toString("base64url");
  pending.set(code, { userId: me.userId, email: me.email, expires: now + CODE_TTL_MS });
  return `vexacapture://connect?code=${encodeURIComponent(code)}&base=${encodeURIComponent(origin)}`;
}

async function myKeys(userId: string | number) {
  const listed = await listUserTokens(userId);
  return (listed.ok ? listed.data ?? [] : []).filter((t) => t.name === KEY_NAME);
}

async function connect(req: NextRequest): Promise<Response> {
  const origin = publicOrigin(req);
  const me = await currentUser();
  if (!me.ok) {
    return html(`<h2>Sign in to Vexa first</h2><p>Open <a href="${esc(origin)}/">${esc(origin)}</a>, sign in, then choose <b>Connect to Vexa</b> in Vexa Capture again.</p>`, 401);
  }
  const link = newLink(me, origin);
  return html(
    `<h2>Connecting Vexa Capture…</h2><p>Signed in as <b>${esc(me.email)}</b>. If Vexa Capture doesn't open, <a id="open" href="${esc(link)}">open it</a>.</p>` +
    `<script>location.href=${JSON.stringify(link)}</script>`,
  );
}

/** Settings → Integrations: the Macs this user has paired (one bot-scoped key each, listed by when each last did anything), and where to get the app. */
async function status(): Promise<Response> {
  const me = await currentUser();
  if (!me.ok) return json({ error: me.error }, me.status);
  const devices = (await myKeys(me.userId)).map((t) => ({ id: t.id, created_at: t.created_at ?? null, last_used_at: t.last_used_at ?? null }));
  return json({ devices, download: downloadUrl() });
}

/** Where this deployment's signed Mac build is published (`CAPTURE_DOWNLOAD_URL`); only an https address is ever handed to the page. */
function downloadUrl(): string | null {
  const raw = (process.env.VEXA_CAPTURE_DOWNLOAD_URL || "").trim();
  try { return new URL(raw).protocol === "https:" ? raw : null; } catch { return null; }
}

/** The same one-time code as the connect page, handed to the card, so pairing can start from where the user already is. */
async function pair(req: NextRequest): Promise<Response> {
  const me = await currentUser();
  if (!me.ok) return json({ error: me.error }, me.status);
  return json({ link: newLink(me, publicOrigin(req)) });
}

/** Disconnect one Mac: revoke its key — only ever one of the signed-in user's own. */
async function revoke(req: NextRequest): Promise<Response> {
  const me = await currentUser();
  if (!me.ok) return json({ error: me.error }, me.status);
  let id = NaN;
  try { id = Number(((await req.json()) as { id?: unknown }).id); } catch { /* → 400 */ }
  if (!Number.isInteger(id)) return json({ error: "Invalid id" }, 400);
  if (!(await myKeys(me.userId)).some((t) => t.id === id)) return json({ error: "Not found" }, 404);
  const done = await revokeToken(id);
  return done.ok ? json({ ok: true }) : json({ error: "Couldn't disconnect that Mac." }, 502);
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

  const mine = (await myKeys(p.userId)).sort((a, b) => b.id - a.id);
  for (const old of mine.slice(KEEP_KEYS - 1)) await revokeToken(old.id);
  const minted = await mintUserToken(p.userId, { scopes: ["bot"], name: KEY_NAME });
  if (!minted.ok || !minted.data?.token) return json({ error: "Couldn't create a key for Vexa Capture." }, 502);
  return json({ key: minted.data.token, api: addr.api, ingest: addr.ingest, account: p.email });
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action === "connect") return connect(req);
  if (action === "status") return status();
  return json({ error: "not_found" }, 404);
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ action: string }> }) {
  const { action } = await ctx.params;
  if (action === "exchange") return exchange(req);
  if (action === "pair") return pair(req);
  if (action === "revoke") return revoke(req);
  return json({ error: "not_found" }, 404);
}
