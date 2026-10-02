import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** /api/zoom/* — one rep's own Zoom connection. The add-on behind it is reachable from the internet, so the relay
 *  must (a) take the user from the validated session, never the request, (b) present the internal secret, and
 *  (c) keep the bot-scoped key it mints in step with the connection: minted on connect, revoked on disconnect. */
let cookieJar: Record<string, string> = {};

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (cookieJar[name] !== undefined ? { name, value: cookieJar[name] } : undefined),
    set: () => {}, delete: () => {},
  }),
}));

import { GET, POST } from "../zoom/[action]/route";

const ctx = (action: string) => ({ params: Promise.resolve({ action }) });
const req = {} as unknown as import("next/server").NextRequest;

interface Recorded { method: string; url: string; body?: unknown; headers?: Record<string, string> }

/** A fake admin-api (alice = user 42, who already has a stale zoom-auto-join key, id 5, and an unrelated key, id 1)
 *  and a fake sales-cycle whose answers each test sets. */
function stub(addon: (path: string, init: Recorded) => Response) {
  const calls: Recorded[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const rec: Recorded = {
      method: init?.method || "GET", url, headers: (init?.headers || {}) as Record<string, string>,
      body: init?.body ? JSON.parse(init.body as string) : undefined,
    };
    calls.push(rec);
    if (url.includes("/internal/validate")) {
      const { token } = JSON.parse((init?.body as string) || "{}");
      return token === "alice-tok" ? new Response(JSON.stringify({ user_id: 42, email: "alice@vexa.ai" }), { status: 200 }) : new Response("no", { status: 401 });
    }
    if (url.includes("/admin/users/42/tokens") && rec.method === "GET") {
      return new Response(JSON.stringify([
        { id: 1, user_id: 42, scopes: ["bot"], name: "ci" }, { id: 5, user_id: 42, scopes: ["bot"], name: "zoom-auto-join" },
      ]), { status: 200 });
    }
    if (url.includes("/admin/users/42/tokens") && rec.method === "POST") {
      return new Response(JSON.stringify({ id: 8, user_id: 42, scopes: ["bot"], name: "zoom-auto-join", token: "vxa_bot_new" }), { status: 201 });
    }
    if (url.includes("/admin/tokens/") && rec.method === "DELETE") return new Response(null, { status: 204 });
    if (url.startsWith("http://sales-cycle.test")) return addon(url.replace("http://sales-cycle.test", ""), rec);
    return new Response("nope", { status: 500 });
  }));
  return calls;
}

const revoked = (calls: Recorded[]) => calls.filter((c) => c.method === "DELETE").map((c) => c.url.split("/").pop());

beforeEach(() => {
  cookieJar = { "vexa-token": "alice-tok" };
  process.env.VEXA_ADMIN_API_URL = "http://admin.test";
  process.env.VEXA_ADMIN_API_KEY = "admin-secret";
  process.env.VEXA_INTERNAL_API_SECRET = "internal-secret";
  process.env.SALES_CYCLE_URL = "http://sales-cycle.test";
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("authorize", () => {
  it("revokes the stale key, mints a bot-scoped one, hands it to the add-on as the session's user, and sends the browser to Zoom", async () => {
    const calls = stub(() => new Response(JSON.stringify({ url: "https://zoom.us/oauth/authorize?state=abc" }), { status: 200 }));
    const res = await GET(req, ctx("authorize"));
    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe("https://zoom.us/oauth/authorize?state=abc");
    expect(revoked(calls)).toEqual(["5"]);                                          // the stale one only, never the unrelated "ci" key
    const mint = calls.find((c) => c.method === "POST" && c.url.includes("/admin/users/42/tokens"))!;
    expect(mint.url).toContain("scopes=bot");
    const link = calls.find((c) => c.url.endsWith("/zoom/authorize-link"))!;
    expect(link.headers!["X-Internal-Secret"]).toBe("internal-secret");
    expect(link.body).toEqual({ vexa_user_id: "42", vexa_token: "vxa_bot_new", vexa_token_id: 8 });
  });

  it("does not leave a minted key behind when the add-on says Zoom is not configured", async () => {
    const calls = stub(() => new Response("{}", { status: 503 }));
    const res = await GET(req, ctx("authorize"));
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/?settings=integrations&zoom_error=not_configured");
    expect(revoked(calls)).toEqual(["5", "8"]);
  });

  it("does not leave a minted key behind when the add-on is unreachable", async () => {
    const calls = stub(() => { throw new Error("ECONNREFUSED"); });
    const res = await GET(req, ctx("authorize"));
    expect(res.headers.get("location")).toBe("/?settings=integrations&zoom_error=unreachable");
    expect(revoked(calls)).toContain("8");
  });

  it("sends a signed-out visitor back to Settings without minting or calling anything", async () => {
    cookieJar = {};
    const calls = stub(() => new Response("{}"));
    const res = await GET(req, ctx("authorize"));
    expect(res.headers.get("location")).toBe("/?settings=integrations&zoom_error=not_signed_in");
    expect(calls.filter((c) => c.url.includes("/zoom/") || c.url.includes("/admin/users"))).toEqual([]);
  });
});

describe("status", () => {
  it("asks the add-on about the session's own user, with the internal secret", async () => {
    const calls = stub(() => new Response(JSON.stringify({ connected: true, configured: true }), { status: 200 }));
    const res = await GET(req, ctx("status"));
    expect(await res.json()).toEqual({ connected: true, configured: true });
    const call = calls.find((c) => c.url.includes("/zoom/status"))!;
    expect(call.url).toContain("vexa_user_id=42");
    expect(call.headers!["X-Internal-Secret"]).toBe("internal-secret");
  });

  it("is a 401 when signed out, and a 502 when the add-on is down", async () => {
    cookieJar = {};
    stub(() => new Response("{}"));
    expect((await GET(req, ctx("status"))).status).toBe(401);
    cookieJar = { "vexa-token": "alice-tok" };
    stub(() => { throw new Error("down"); });
    expect((await GET(req, ctx("status"))).status).toBe(502);
  });
});

describe("disconnect", () => {
  it("forgets the connection and revokes the user's own key", async () => {
    const calls = stub(() => new Response(JSON.stringify({ connected: false, vexa_token_id: "5" }), { status: 200 }));
    const res = await POST(req, ctx("disconnect"));
    expect(await res.json()).toEqual({ connected: false });
    expect(calls.find((c) => c.url.endsWith("/zoom/disconnect"))!.body).toEqual({ vexa_user_id: "42" });
    expect(revoked(calls)).toEqual(["5"]);
  });

  it("never revokes a key that is not in the user's own token list", async () => {
    const calls = stub(() => new Response(JSON.stringify({ connected: false, vexa_token_id: "999" }), { status: 200 }));
    await POST(req, ctx("disconnect"));
    expect(revoked(calls)).toEqual([]);
  });
});

describe("authorize also registers the meeting.started webhook", () => {
  it("points the user's webhook at the add-on, so a Zoom-joined bot is captured like any other", async () => {
    process.env.SALES_CYCLE_URL = "http://sales-cycle.test";
    process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET = "shh";
    vi.resetModules();
    const { GET: freshGet } = await import("../zoom/[action]/route");
    const calls = stub((path) => path === "/zoom/authorize-link"
      ? new Response(JSON.stringify({ url: "https://zoom.us/oauth/authorize?state=abc" }), { status: 200 })
      : new Response("{}", { status: 200 }));
    // (the stub answers the gateway's /user/webhook GET/PUT with 500 by default; make them succeed)
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/user/webhook")) {
        calls.push({ method: init?.method || "GET", url, body: init?.body ? JSON.parse(init.body as string) : undefined });
        return new Response(JSON.stringify({}), { status: 200 });
      }
      return original(url, init);
    }));
    const res = await freshGet(req, ctx("authorize"));
    expect(res.headers.get("location")).toContain("zoom.us");
    const put = calls.find((c) => c.method === "PUT" && c.url.endsWith("/user/webhook"))!;
    expect(put.body).toMatchObject({ webhook_url: "http://sales-cycle.test/webhooks/meeting-started", webhook_events: { "meeting.started": true } });
    delete process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET;
  });
});

it("answers 404 for an action it does not have", async () => {
  stub(() => new Response("{}"));
  expect((await GET(req, ctx("bogus"))).status).toBe(404);
  expect((await POST(req, ctx("authorize"))).status).toBe(404);
});
