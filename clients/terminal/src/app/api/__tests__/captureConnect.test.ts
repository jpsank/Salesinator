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
interface Row { id: number; user_id: number; scopes: string[]; name: string }
const macs = (n: number): Row[] => [...Array(n).keys()].map((i) => ({ id: i + 1, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac)" }));
let rows: Row[] = [];
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
      return new Response(JSON.stringify(rows.concat([{ id: 9, user_id: 42, scopes: ["bot"], name: "ci" }])), { status: 200 });
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
  rows = macs(5);
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

describe("the Settings card's routes", () => {
  it("status lists the user's own paired Macs, and nothing else they hold", async () => {
    stub();
    const res = await GET(getReq(), ctx("status"));
    const body = await res.json() as { devices: Array<{ id: number }> };
    expect(body.devices.map((d) => d.id)).toEqual([1, 2, 3, 4, 5]);               // the "ci" key (id 9) is not a Mac
  });

  it("status carries the published download address only when it is https", async () => {
    stub();
    const prior = process.env.VEXA_CAPTURE_DOWNLOAD_URL;
    try {
      for (const [set, want] of [["https://example.com/v.dmg", "https://example.com/v.dmg"], ["http://example.com/v.dmg", null], ["javascript:alert(1)", null], ["", null]] as const) {
        process.env.VEXA_CAPTURE_DOWNLOAD_URL = set;
        expect(((await (await GET(getReq(), ctx("status"))).json()) as { download: string | null }).download).toBe(want);
      }
    } finally { if (prior === undefined) delete process.env.VEXA_CAPTURE_DOWNLOAD_URL; else process.env.VEXA_CAPTURE_DOWNLOAD_URL = prior; }
  });

  it("status names each Mac from its key, and says nothing for a key from before names", async () => {
    rows = [{ id: 1, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac)" }, { id: 2, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac) · Julian's MacBook Pro · 1a2b3c4d" }];
    stub();
    const body = await (await GET(getReq(), ctx("status"))).json() as { devices: Array<{ id: number; name: string | null }> };
    expect(body.devices.map((d) => [d.id, d.name])).toEqual([[1, null], [2, "Julian's MacBook Pro"]]);
  });

  it("status and pair need a signed-in user", async () => {
    cookieJar = {};
    stub();
    expect((await GET(getReq(), ctx("status"))).status).toBe(401);
    expect((await POST(postReq({}), ctx("pair"))).status).toBe(401);
    expect((await POST(postReq({ id: 1 }), ctx("revoke"))).status).toBe(401);
  });

  it("pair hands the card a link carrying a code the app can redeem, and mints no key", async () => {
    const calls = stub();
    const res = await POST(postReq({}), ctx("pair"));
    const { link } = await res.json() as { link: string };
    expect(link).toMatch(/^vexacapture:\/\/connect\?code=[A-Za-z0-9_-]{20,}&base=http%3A%2F%2Flocalhost%3A13000$/);
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/tokens"))).toBe(false);
    const code = decodeURIComponent(link.match(/code=([^&]+)/)![1]);
    expect((await POST(postReq({ code }), ctx("exchange"))).status).toBe(200);    // the same code the connect page makes
  });

  it("revoke disconnects one of the user's own Macs", async () => {
    const calls = stub();
    const res = await POST(postReq({ id: 3 }), ctx("revoke"));
    expect(res.status).toBe(200);
    expect(calls.filter((c) => c.method === "DELETE").map((c) => c.url.split("/").pop())).toEqual(["3"]);
  });

  it("revoke refuses a key that is not one of their Macs (another user's, or a different kind of key)", async () => {
    const calls = stub();
    expect((await POST(postReq({ id: 9 }), ctx("revoke"))).status).toBe(404);     // their own "ci" key
    expect((await POST(postReq({ id: 777 }), ctx("revoke"))).status).toBe(404);
    expect((await POST(postReq({ id: "x" }), ctx("revoke"))).status).toBe(400);
    expect((await POST(postReq({}), ctx("revoke"))).status).toBe(400);
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
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

describe("exchange with a Mac's identity", () => {
  const device = { id: "1a2b3c4d-0000-4000-8000-000000000000", name: "Julian's MacBook Pro" };
  const exchangeWith = async (dev: unknown) => {
    const calls = stub();
    const code = await codeFrom(await GET(getReq(), ctx("connect")));
    const res = await POST(postReq({ code, device: dev }), ctx("exchange"));
    return { res, calls, revoked: calls.filter((c) => c.method === "DELETE").map((c) => c.url.split("/").pop()), mint: calls.find((c) => c.method === "POST" && c.url.includes("/admin/users/42/tokens")) };
  };

  it("names the new key after the Mac", async () => {
    const { res, mint } = await exchangeWith(device);
    expect(res.status).toBe(200);
    expect(decodeURIComponent(mint!.url.replace(/\+/g, " "))).toContain("name=vexa-capture (Mac) · Julian's MacBook Pro · 1a2b3c4d");
  });

  it("pairing the same Mac again replaces its earlier key instead of adding another", async () => {
    rows = [
      { id: 1, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac) · Julian's MacBook Pro · 1a2b3c4d" },
      { id: 2, user_id: 42, scopes: ["bot"], name: "vexa-capture (Mac) · Office iMac · ffeeddcc" },
    ];
    const { revoked } = await exchangeWith(device);
    expect(revoked).toEqual(["1"]);                                          // the other Mac's key is untouched
  });

  it("ignores a malformed identity and cleans up the name", async () => {
    expect((await exchangeWith({ id: "x", name: "bad" })).mint!.url).toMatch(/name=vexa-capture\+%28Mac%29$/);
    const noisy = await exchangeWith({ id: device.id, name: "A\u0000B · C" + "x".repeat(80) });
    const name = decodeURIComponent(noisy.mint!.url.replace(/\+/g, " ")).split("name=")[1];
    expect(name.split(" · ")).toHaveLength(3);                                // the separator inside a name cannot forge a second field
    expect(name).not.toContain("\u0000");
  });
});
