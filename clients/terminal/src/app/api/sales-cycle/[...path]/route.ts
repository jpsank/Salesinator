/** Proxies /api/sales-cycle/* to the sales-cycle add-on's own backend (integrations/sales-cycle/) —
 *  mirrors the pattern of the main /api/[...path] catch-all (../[...path]/route.ts), but for a
 *  DIFFERENT backend host, and without X-API-Key: the connections this fronts (HubSpot, Slack, …)
 *  are shared/deployment-wide, not tied to whichever Vexa account happens to be logged in, so
 *  there's no per-user identity to forward here. Kept as its own file rather than folded into the
 *  main proxy, since it targets a different host entirely (SALES_CYCLE_URL, not GATEWAY_URL).
 */
import type { NextRequest } from "next/server";
import { requireAdmin, requireUser } from "../../admin/gate";

export const dynamic = "force-dynamic";

const SALES_CYCLE_URL = (process.env.SALES_CYCLE_URL || "http://127.0.0.1:18300").replace(/\/$/, "");

/** Only the paths the terminal's own client calls (salesCycleApi.ts) — the backend's /internal/* and
 *  /dispatch endpoints are never reachable through the browser-facing proxy. */
const ALLOWED_PATHS = [/^oauth\/[a-z0-9_-]+\/(status|disconnect|token)$/, /^slack\/channel(-status)?$/];

const deny = (status: number, error: string) =>
  new Response(JSON.stringify({ error }), { status, headers: { "Content-Type": "application/json" } });

async function forward(req: NextRequest, params: Promise<{ path: string[] }>): Promise<Response> {
  const { path } = await params;
  if (!ALLOWED_PATHS.some((re) => re.test(path.join("/")))) return deny(404, "not_found");
  // The connections are deployment-wide: reading needs a signed-in user, changing them needs an admin.
  if (!(req.method === "GET" ? await requireUser() : await requireAdmin())) return deny(req.method === "GET" ? 401 : 403, "forbidden");
  const url = `${SALES_CYCLE_URL}/${path.join("/")}${req.nextUrl.search}`;

  const init: RequestInit = { method: req.method, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "DELETE") {
    const body = await req.text();
    if (body) {
      init.body = body;
      init.headers = { "Content-Type": "application/json" };
    }
  }

  try {
    const upstream = await fetch(url, init);
    if (upstream.status === 204 || upstream.status === 205 || upstream.status === 304) {
      return new Response(null, { status: upstream.status, headers: { "Cache-Control": "no-cache" } });
    }
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-cache" },
    });
  } catch (err) {
    // upstream unreachable — FAIL LOUD (P18), never a silent empty {} that reads as "not connected".
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return new Response(JSON.stringify({ error: "upstream_unreachable", detail }), { status: 502, headers: { "Content-Type": "application/json" } });
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export const GET = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
export const POST = (req: NextRequest, ctx: Ctx) => forward(req, ctx.params);
