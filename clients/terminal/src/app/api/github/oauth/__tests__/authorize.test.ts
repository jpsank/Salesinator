import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../proxyAuth", () => ({ resolveApiKey: async () => "alice-tok" }));

import { GET } from "../authorize/route";

function makeReq(search = ""): import("next/server").NextRequest {
  return { nextUrl: new URL(`http://x${search}`) } as unknown as import("next/server").NextRequest;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("GET /api/github/oauth/authorize", () => {
  it("relays the redirect Location as a real browser navigation", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, {
      status: 302, headers: { Location: "https://github.com/login/oauth/authorize?client_id=cid" },
    })));
    const res = await GET(makeReq());
    expect(res.status).toBe(307);  // NextResponse.redirect's default
    expect(res.headers.get("location")).toBe("https://github.com/login/oauth/authorize?client_id=cid");
  });

  it("forwards ?for= to agent-api unchanged, WITHOUT a doubled /api/ segment", async () => {
    // Regression guard: the gateway's own rule is /agent/<path> -> agent-api/api/<path> (it adds
    // /api/ itself — see core/gateway/.../app.py's `_agent()`), so this route must NOT also
    // include "api/" in what it asks the gateway for, or every call 404s on a doubled /api/api/.
    const fetchSpy = vi.fn(async () => new Response(null, { status: 302, headers: { Location: "https://github.com/x" } }));
    vi.stubGlobal("fetch", fetchSpy);
    await GET(makeReq("?for=product-repo"));
    const [url] = fetchSpy.mock.calls[0] as unknown as [string];
    expect(url).toBe("http://127.0.0.1:18056/agent/workspace/git-token/oauth/authorize?for=product-repo");
  });

  it("omits the query entirely when no ?for= is given (the personal-card path, unchanged)", async () => {
    const fetchSpy = vi.fn(async () => new Response(null, { status: 302, headers: { Location: "https://github.com/x" } }));
    vi.stubGlobal("fetch", fetchSpy);
    await GET(makeReq());
    const [url] = fetchSpy.mock.calls[0] as unknown as [string];
    expect(url).toBe("http://127.0.0.1:18056/agent/workspace/git-token/oauth/authorize");
  });

  it("surfaces the real body/status (e.g. 503 not configured) instead of a blind redirect when there's no Location", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "not configured" }), { status: 503 })));
    const res = await GET(makeReq());
    expect(res.status).toBe(503);
    expect((await res.json()).detail).toBe("not configured");
  });

  it("returns 502 when agent-api is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await GET(makeReq());
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("upstream_unreachable");
  });
});
