import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** /api/sales-cycle/* fronts deployment-wide connections (HubSpot, Slack) and a backend with
 *  unauthenticated routes, so the proxy itself must gate: signed-in to read, admin to change, and only
 *  the paths the terminal's own client calls. */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
  }),
}));

import { GET, POST } from "../sales-cycle/[...path]/route";

function stubFetch() {
  const upstream: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.includes("/internal/validate")) {
      const { token } = JSON.parse((init?.body as string) || "{}");
      if (token === "user-tok") return new Response(JSON.stringify({ user_id: 2, email: "bob@example.com" }), { status: 200 });
      if (token === "admin-tok") return new Response(JSON.stringify({ user_id: 1, email: "a@example.com", is_admin: true }), { status: 200 });
      return new Response("no", { status: 401 });
    }
    upstream.push(url);
    return new Response("{}", { status: 200 });
  }));
  return upstream;
}

const req = (method: string) => ({ method, nextUrl: { search: "" }, text: async () => "" }) as unknown as import("next/server").NextRequest;
const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });

beforeEach(() => {
  cookieJar = {};
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("sales-cycle proxy gate", () => {
  it("refuses an anonymous GET and POST without reaching the backend", async () => {
    const upstream = stubFetch();
    expect((await GET(req("GET"), ctx("oauth", "slack", "status"))).status).toBe(401);
    expect((await POST(req("POST"), ctx("oauth", "slack", "token"))).status).toBe(403);
    expect(upstream).toEqual([]);
  });

  it("lets a signed-in non-admin read but not change a connection", async () => {
    cookieJar["vexa-token"] = "user-tok";
    const upstream = stubFetch();
    expect((await GET(req("GET"), ctx("oauth", "hubspot", "status"))).status).toBe(200);
    expect((await POST(req("POST"), ctx("oauth", "hubspot", "disconnect"))).status).toBe(403);
    expect(upstream).toHaveLength(1);
  });

  it("lets an admin change a connection", async () => {
    cookieJar["vexa-token"] = "admin-tok";
    stubFetch();
    expect((await POST(req("POST"), ctx("slack", "channel"))).status).toBe(200);
  });

  it("presents the internal secret to the (published) backend — only after it has verified the user", async () => {
    cookieJar["vexa-token"] = "admin-tok";
    const seen: Array<Record<string, string>> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/internal/validate")) return new Response(JSON.stringify({ user_id: 1, email: "a@example.com", is_admin: true }), { status: 200 });
      seen.push((init?.headers || {}) as Record<string, string>);
      return new Response("{}", { status: 200 });
    }));
    await GET(req("GET"), ctx("oauth", "slack", "status"));
    await POST(req("POST"), ctx("slack", "channel"));
    expect(seen.map((h) => h["X-Internal-Secret"])).toEqual(["internal-secret", "internal-secret"]);
  });

  it("never proxies the backend's internal or dispatch routes, even for an admin", async () => {
    cookieJar["vexa-token"] = "admin-tok";
    const upstream = stubFetch();
    for (const p of [["internal", "process-approved"], ["internal", "sweep-live-watchers"], ["dispatch"]]) {
      expect((await POST(req("POST"), ctx(...p))).status).toBe(404);
    }
    expect(upstream).toEqual([]);
  });
});
