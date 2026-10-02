import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** /api/capture/{connect,exchange} — pairing the Mac app. The properties that matter: the code names a user the server
 *  authenticated, works once and briefly, and the key is only minted — and only returned — at exchange. */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: () => {}, delete: () => {},
  }),
}));

import { GET, POST } from "../capture/[action]/route";

const ctx = (action: string) => ({ params: Promise.resolve({ action }) });
function getReq(host = "localhost:13000", extra: Record<string, string> = {}) {
  return { headers: new Headers({ host, ...extra }), nextUrl: { host } } as unknown as import("next/server").NextRequest;
}
function postReq(body: unknown, host = "localhost:13000", extra: Record<string, string> = {}) {
  return { headers: new Headers({ host, ...extra }), nextUrl: { host }, json: async () => body } as unknown as import("next/server").NextRequest;
}

interface Rec { method: string; url: string }
function stub() {
  const calls: Rec[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method || "GET";
    calls.push({ method, url });
    if (url.includes("/internal/validate")) {
      const { token } = JSON.parse((init?.body as string) || "{}");
      return token === "alice-tok" ? new Response(JSON.stringify({ user_id: 42, email: "alice@vexa.ai" }), { status: 200 }) : new Response("no", { status: 401 });
    }
    if (url.includes("/admin/users/42/tokens") && method === "GET") {
      return new Response(JSON.stringify([1, 2, 3, 4, 5].map((id) => ({ id, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac)" })).concat([{ id: 9, user_id: 42, scopes: ["bot"], name: "ci" }])), { status: 200 });
    }
    if (url.includes("/admin/users/42/tokens") && method === "POST") return new Response(JSON.stringify({ id: 10, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac)", token: "vxa_bot_new" }), { status: 201 });
    if (url.includes("/admin/tokens/") && method === "DELETE") return new Response(null, { status: 204 });
    return new Response("nope", { status: 500 });
  }));
  return calls;
}

const codeFrom = async (res: Response): Promise<string> => {
  const m = (await res.text()).match(/vexacapture:\/\/connect\?code=([^&"]+)/);
  return m ? decodeURIComponent(m[1]) : "";
};

beforeEach(() => {
  cookieJar = { "vexa-token": "alice-tok" };
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-secret";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  delete process.env.VEXA_CAPTURE_API_URL; delete process.env.VEXA_CAPTURE_INGEST_URL;
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("connect", () => {
  it("shows a signed-in user a page that opens the app with a one-time code and this site's address — and mints no key yet", async () => {
    const calls = stub();
    const res = await GET(getReq(), ctx("connect"));
    expect(res.status).toBe(200);
    const body = await res.text();
    expect(body).toContain("alice@vexa.ai");
    expect(body).toMatch(/vexacapture:\/\/connect\?code=[A-Za-z0-9_-]{20,}&amp;base=http%3A%2F%2Flocalhost%3A13000|vexacapture:\/\/connect\?code=[A-Za-z0-9_-]{20,}&base=http%3A%2F%2Flocalhost%3A13000/);
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/tokens"))).toBe(false);
  });

  it("names the public origin behind a proxy", async () => {
    stub();
    process.env.VEXA_CAPTURE_API_URL = "https://api.x.com"; process.env.VEXA_CAPTURE_INGEST_URL = "wss://cap.x.com/ingest";
    const res = await GET(getReq("terminal:3000", { "x-forwarded-host": "terminal.example.com", "x-forwarded-proto": "https" }), ctx("connect"));
    expect(await res.text()).toContain("base=https%3A%2F%2Fterminal.example.com");
  });

  it("asks a signed-out visitor to sign in, with no code", async () => {
    cookieJar = {};
    stub();
    const res = await GET(getReq(), ctx("connect"));
    expect(res.status).toBe(401);
    expect(await res.text()).not.toContain("vexacapture://");
  });

  it("needs no per-deployment setup: a site with nothing configured still pairs", async () => {
    stub();
    const res = await GET(getReq("terminal.example.com", { "x-forwarded-proto": "https" }), ctx("connect"));
    expect(res.status).toBe(200);
    expect(await codeFrom(res)).not.toBe("");
  });
});

describe("exchange", () => {
  it("trades the code, once, for a bot-scoped key and the addresses — and prunes the user's old keys", async () => {
    const calls = stub();
    const code = await codeFrom(await GET(getReq(), ctx("connect")));
    expect(code).not.toBe("");
    const res = await POST(postReq({ code }), ctx("exchange"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ key: "vxa_bot_new", api: "http://localhost:13000/api/capture/relay", ingest: "ws://localhost:13000/capture/ingest", account: "alice@vexa.ai" });
    const mint = calls.find((c) => c.method === "POST" && c.url.includes("/admin/users/42/tokens"))!;
    expect(mint.url).toContain("scopes=bot");
    expect(mint.url).toContain("name=vexa-capture");
    const revoked = calls.filter((c) => c.method === "DELETE").map((c) => c.url.split("/").pop());
    expect(revoked).toEqual(["2", "1"]);                                  // newest 3 kept + the new one = 4; the "ci" key is untouched
    const again = await POST(postReq({ code }), ctx("exchange"));
    expect(again.status).toBe(404);                                       // single use
  });

  it("points the app at this site's own relays — one public address — on a deployment too", async () => {
    stub();
    const hdr = { "x-forwarded-host": "terminal.example.com", "x-forwarded-proto": "https" };
    const code = await codeFrom(await GET(getReq("terminal:3000", hdr), ctx("connect")));
    const res = await POST(postReq({ code }, "terminal:3000", hdr), ctx("exchange"));
    expect(await res.json()).toMatchObject({ api: "https://terminal.example.com/api/capture/relay", ingest: "wss://terminal.example.com/capture/ingest" });
  });

  it("uses the configured addresses instead when the deployment publishes the services itself", async () => {
    stub();
    process.env.VEXA_CAPTURE_API_URL = "https://api.x.com"; process.env.VEXA_CAPTURE_INGEST_URL = "wss://cap.x.com/ingest";
    const code = await codeFrom(await GET(getReq("terminal.example.com", { "x-forwarded-proto": "https" }), ctx("connect")));
    const res = await POST(postReq({ code }, "terminal.example.com", { "x-forwarded-proto": "https" }), ctx("exchange"));
    expect(await res.json()).toMatchObject({ api: "https://api.x.com", ingest: "wss://cap.x.com/ingest" });
  });

  it("refuses an unknown code and a missing one, without minting anything", async () => {
    const calls = stub();
    expect((await POST(postReq({ code: "nope" }), ctx("exchange"))).status).toBe(404);
    expect((await POST(postReq({}), ctx("exchange"))).status).toBe(404);
    expect(calls.some((c) => c.url.includes("/tokens"))).toBe(false);
  });

  it("refuses a code after two minutes", async () => {
    stub();
    vi.useFakeTimers();
    const code = await codeFrom(await GET(getReq(), ctx("connect")));
    vi.advanceTimersByTime(121_000);
    expect((await POST(postReq({ code }), ctx("exchange"))).status).toBe(404);
  });

  it("answers 404 for an action it does not have", async () => {
    stub();
    expect((await GET(getReq(), ctx("bogus"))).status).toBe(404);
    expect((await POST(postReq({}), ctx("connect"))).status).toBe(404);
  });
});
