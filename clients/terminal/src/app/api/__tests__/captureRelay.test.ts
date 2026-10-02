import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** /api/capture/relay — Vexa Capture's bot requests through this site. The point of the route is what it will NOT do: it never
 *  authenticates as the browser user or the deployment, only as the key the app sends, and it relays two calls and nothing else. */
let cookieJar: Record<string, string> = {};
vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (n: string) => (cookieJar[n] !== undefined ? { name: n, value: cookieJar[n] } : undefined) }),
}));

import { DELETE, POST } from "../capture/relay/[...path]/route";

const ctx = (...path: string[]) => ({ params: Promise.resolve({ path }) });
const req = (method: string, headers: Record<string, string> = {}, body = "") =>
  ({ method, headers: new Headers(headers), text: async () => body }) as unknown as import("next/server").NextRequest;

interface Seen { url: string; method: string; headers: Record<string, string>; body?: string }
function gateway(status = 201, body: unknown = { id: 9, platform: "zoom", native_meeting_id: "81234567890" }) {
  const seen: Seen[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    seen.push({ url, method: init?.method || "GET", headers: (init?.headers || {}) as Record<string, string>, body: init?.body as string | undefined });
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  }));
  return seen;
}

beforeEach(() => {
  cookieJar = {};
  process.env.GATEWAY_URL = "http://gateway.test:8000";
  process.env.VEXA_API_KEY = "deployment-key";
  process.env.VEXA_BOT_API_KEY = "deployment-bot-key";
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("POST /bots", () => {
  it("relays the app's request to the gateway under the APP's key", async () => {
    const seen = gateway();
    const res = await POST(req("POST", { "x-api-key": "app-key" }, '{"meeting_url":"https://zoom.us/j/81234567890"}'), ctx("bots"));
    expect(res.status).toBe(201);
    expect(await res.json()).toMatchObject({ platform: "zoom" });
    expect(seen[0].url).toBe("http://gateway.test:8000/bots");
    expect(seen[0].headers["X-API-Key"]).toBe("app-key");
    expect(seen[0].body).toBe('{"meeting_url":"https://zoom.us/j/81234567890"}');
  });

  it("refuses a request with no key — it never falls back to the deployment's key or a browser cookie", async () => {
    const seen = gateway();
    cookieJar = { "vexa-token": "someones-browser-session" };
    const res = await POST(req("POST", {}, "{}"), ctx("bots"));
    expect(res.status).toBe(401);
    expect(seen).toEqual([]);
    expect((await POST(req("POST", { "x-api-key": "   " }, "{}"), ctx("bots"))).status).toBe(401);
  });

  it("passes the gateway's refusals through unchanged (a bad key is the gateway's 401)", async () => {
    gateway(401, { detail: "Invalid API key" });
    const res = await POST(req("POST", { "x-api-key": "bad" }, "{}"), ctx("bots"));
    expect(res.status).toBe(401);
    expect(await res.json()).toEqual({ detail: "Invalid API key" });
  });

  it("refuses a body over 16 KB", async () => {
    const seen = gateway();
    expect((await POST(req("POST", { "x-api-key": "k" }, "x".repeat(17 * 1024)), ctx("bots"))).status).toBe(413);
    expect(seen).toEqual([]);
  });

  it("is a 502 when the gateway is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));
    expect((await POST(req("POST", { "x-api-key": "k" }, "{}"), ctx("bots"))).status).toBe(502);
  });
});

describe("DELETE /bots/{platform}/{id}", () => {
  it("relays removing the bot, under the app's key", async () => {
    const seen = gateway(200, { status: "stopping" });
    const res = await DELETE(req("DELETE", { "x-api-key": "app-key" }), ctx("bots", "zoom", "81234567890"));
    expect(res.status).toBe(200);
    expect(seen[0]).toMatchObject({ url: "http://gateway.test:8000/bots/zoom/81234567890", method: "DELETE" });
    expect(seen[0].headers["X-API-Key"]).toBe("app-key");
  });

  it("needs the key too", async () => {
    gateway();
    expect((await DELETE(req("DELETE"), ctx("bots", "zoom", "1"))).status).toBe(401);
  });
});

describe("only those two calls", () => {
  it("relays no other path, on either method", async () => {
    const seen = gateway();
    for (const p of [["meetings"], ["bots", "status"], ["admin", "users"], ["agent", "chat"], ["bots", "zoom"], ["bots", "../admin", "x"], ["bots", "Zoom", "1"]]) {
      expect((await POST(req("POST", { "x-api-key": "k" }, "{}"), ctx(...p))).status).toBe(404);
      expect((await DELETE(req("DELETE", { "x-api-key": "k" }), ctx(...p))).status).toBe(404);
    }
    expect((await POST(req("POST", { "x-api-key": "k" }, "{}"), ctx("bots", "zoom", "1"))).status).toBe(404);   // POST is only for /bots
    expect(seen).toEqual([]);
  });
});
