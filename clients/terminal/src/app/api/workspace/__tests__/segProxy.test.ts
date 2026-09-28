import { afterEach, describe, expect, it, vi } from "vitest";

/** DELETE /api/workspace/{slug} (workspaceApi.deleteWorkspace) 405'd forever: this file only ever
 *  exported GET/POST, so Next.js auto-405'd any DELETE before it reached agent-api's own working
 *  `DELETE /api/workspace/{slug}` (control_plane/api.py:2044) at all. */

vi.mock("../../proxyAuth", () => ({ resolveApiKey: async () => "alice-tok" }));
vi.mock("../../../mode", () => ({ meetingsOnly: () => false }));

import { DELETE as deleteRoute, GET as getRoute } from "../[...seg]/route";

function makeReq(): import("next/server").NextRequest {
  return { nextUrl: { search: "" } } as unknown as import("next/server").NextRequest;
}
const ctx = (...seg: string[]) => ({ params: Promise.resolve({ seg }) });

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("workspace/[...seg] proxy", () => {
  it("forwards DELETE to agent-api and passes the status + body through", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ slug: "cust-1", deleted: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const res = await deleteRoute(makeReq(), ctx("cust-1"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ slug: "cust-1", deleted: true });

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/agent/workspace/cust-1");
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("alice-tok");
  });

  it("still surfaces a real backend rejection (e.g. can't delete the active workspace)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "cannot delete active workspace" }), { status: 409 })));
    const res = await deleteRoute(makeReq(), ctx("cust-1"));
    expect(res.status).toBe(409);
  });

  it("returns 502 when agent-api is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    const res = await deleteRoute(makeReq(), ctx("cust-1"));
    expect(res.status).toBe(502);
  });

  it("GET still works unchanged (regression guard for the same file)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ content: "hi" }), { status: 200 })));
    const res = await getRoute(makeReq(), ctx("file"));
    expect(res.status).toBe(200);
  });
});
