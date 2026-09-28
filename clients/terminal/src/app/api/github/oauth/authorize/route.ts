/** "Connect GitHub" — the one leg of this OAuth flow that DOES need the caller's Vexa identity
 *  (agent-api's own git-token/oauth/authorize signs the logged-in subject into GitHub's `state`,
 *  so the callback — a plain redirect from github.com, no cookie — knows whose token it is).
 *  Always the caller's own identity — a product repo or other shared workspace reuses this SAME
 *  personal connection rather than needing its own separate "Connect" (see workspaceApi's
 *  `forSubject` params on init/attached/swap).
 *
 *  A normal <a href> link can't carry the X-API-Key the gateway needs to resolve that identity, so
 *  this does what the main proxy does for every OTHER call — resolve the key server-side, forward
 *  it — but for a REDIRECT response, not JSON: fetch agent-api's authorize endpoint with
 *  redirect:"manual", read the Location it hands back (GitHub's real consent URL, already carrying
 *  the signed state), and 302 the ACTUAL browser there. Two hops, one real navigation from the
 *  user's point of view.
 */
import { NextResponse } from "next/server";
import { resolveApiKey } from "../../../proxyAuth";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

export async function GET() {
  const apiKey = await resolveApiKey();
  let upstream: Response;
  try {
    upstream = await fetch(`${GATEWAY_URL}/agent/workspace/git-token/oauth/authorize`, {
      headers: { "X-API-Key": apiKey }, redirect: "manual", cache: "no-store",
    });
  } catch (err) {
    const detail = err instanceof Error && err.message ? err.message : "upstream unreachable";
    return new Response(JSON.stringify({ error: "upstream_unreachable", detail }), { status: 502, headers: { "Content-Type": "application/json" } });
  }
  const location = upstream.headers.get("location");
  if (!location) {
    // Not configured (503) or an auth failure — surface the real body/status rather than a blind redirect.
    return new Response(await upstream.text(), { status: upstream.status, headers: { "Content-Type": "application/json" } });
  }
  return NextResponse.redirect(location);
}
