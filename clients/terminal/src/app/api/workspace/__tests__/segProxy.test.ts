import { afterEach, describe, expect, it, vi } from "vitest";

/** DELETE /api/workspace/{slug} (workspaceApi.deleteWorkspace) 405'd forever: this file only ever
 *  exported GET/POST, so Next.js auto-405'd any DELETE before it reached agent-api's own working
 *  `DELETE /api/workspace/{slug}` (control_plane/api.py:2044) at all. */

vi.mock("../../proxyAuth", () => ({ resolveApiKey: async () => "alice-tok" }));
vi.mock("../../../mode", () => ({ meetingsOnly: () => false }));
const requireAdmin = vi.fn(async () => null as unknown);
vi.mock("../../admin/gate", () => ({ requireAdmin: () => requireAdmin() }));

import { DELETE as deleteRoute, GET as getRoute, POST as postRoute } from "../[...seg]/route";

function makeReq(): import("next/server").NextRequest {
  return { nextUrl: { search: "", searchParams: new URLSearchParams() } } as unknown as import("next/server").NextRequest;
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
describe("workspace/[...seg] proxy — the shared product-repo identity is admin-only", () => {
  const reqWith = (search: string, body = ""): import("next/server").NextRequest => ({
    nextUrl: { search, searchParams: new URLSearchParams(search) },
    text: async () => body, body: null, headers: new Headers(),
  } as unknown as import("next/server").NextRequest);

  it("refuses a non-admin acting for the shared identity (?for= and swap's for_subject) without calling agent-api", async () => {
    requireAdmin.mockResolvedValue(null);
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    expect((await getRoute(reqWith("?for=product-repo"), ctx("attached"))).status).toBe(403);
    expect((await postRoute(reqWith("?for=product-repo"), ctx("init"))).status).toBe(403);
    expect((await postRoute(reqWith("", JSON.stringify({ repo: "x", for_subject: "product-repo" })), ctx("swap"))).status).toBe(403);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("lets a non-admin use their own workspace, and an admin act for the shared one", async () => {
    const fetchSpy = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    requireAdmin.mockResolvedValue(null);
    expect((await getRoute(reqWith(""), ctx("attached"))).status).toBe(200);
    expect((await postRoute(reqWith("", JSON.stringify({ repo: "x", for_subject: null })), ctx("swap"))).status).toBe(200);
    requireAdmin.mockResolvedValue({ email: "a@b.c", userId: 1 });
    expect((await getRoute(reqWith("?for=product-repo"), ctx("attached"))).status).toBe(200);
    expect((await postRoute(reqWith("", JSON.stringify({ for_subject: "product-repo" })), ctx("swap"))).status).toBe(200);
  });
});
});
