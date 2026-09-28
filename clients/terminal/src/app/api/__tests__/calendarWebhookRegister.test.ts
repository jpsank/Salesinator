import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** app/api/calendar/register-webhook/route.ts reads its config as module-level consts, so each
 *  test sets env vars THEN re-imports the module fresh (vi.resetModules) to pick them up — the
 *  same pattern surfaces/__tests__/liveMeetings.store.test.tsx uses for a module-level singleton. */

vi.mock("../proxyAuth", () => ({ resolveApiKey: async () => "alice-tok" }));

const ENV_KEYS = ["GATEWAY_URL", "SALES_CYCLE_URL", "SALES_CYCLE_CALENDAR_WEBHOOK_SECRET"] as const;
const savedEnv: Record<string, string | undefined> = {};

beforeEach(() => {
  vi.resetModules();
  for (const k of ENV_KEYS) savedEnv[k] = process.env[k];
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  for (const k of ENV_KEYS) {
    if (savedEnv[k] === undefined) delete process.env[k];
    else process.env[k] = savedEnv[k];
  }
});

async function loadRoute() {
  return import("../calendar/register-webhook/route");
}

describe("POST /api/calendar/register-webhook", () => {
  it("no-ops when sales-cycle isn't configured on this deployment", async () => {
    delete process.env.SALES_CYCLE_URL;
    delete process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET;
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await loadRoute();
    const res = await POST();
    expect(await res.json()).toEqual({ registered: false, notConfigured: true });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("registers the webhook when the account has none configured yet", async () => {
    process.env.GATEWAY_URL = "http://gateway:8000";
    process.env.SALES_CYCLE_URL = "http://sales-cycle:8200";
    process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET = "shh";
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (!init || init.method === undefined) return new Response(JSON.stringify({}), { status: 200 });
      expect(url).toBe("http://gateway:8000/user/webhook");
      expect(init.method).toBe("PUT");
      const body = JSON.parse(init.body as string);
      expect(body).toEqual({
        webhook_url: "http://sales-cycle:8200/webhooks/meeting-started",
        webhook_secret: "shh",
        webhook_events: { "meeting.started": true },
      });
      return new Response(JSON.stringify({}), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await loadRoute();
    const res = await POST();
    expect(await res.json()).toEqual({ registered: true });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("is a no-op (skips the PUT) when already correctly registered", async () => {
    process.env.GATEWAY_URL = "http://gateway:8000";
    process.env.SALES_CYCLE_URL = "http://sales-cycle:8200";
    process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET = "shh";
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      webhook_url: "http://sales-cycle:8200/webhooks/meeting-started",
      webhook_events: { "meeting.started": true },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await loadRoute();
    const res = await POST();
    expect(await res.json()).toEqual({ registered: true, already: true });
    expect(fetchSpy).toHaveBeenCalledTimes(1);  // GET only — no PUT needed
  });

  it("never overwrites a different webhook the account already has configured", async () => {
    process.env.GATEWAY_URL = "http://gateway:8000";
    process.env.SALES_CYCLE_URL = "http://sales-cycle:8200";
    process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET = "shh";
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({
      webhook_url: "https://example.com/my-own-hook",
      webhook_events: { "meeting.completed": true },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);

    const { POST } = await loadRoute();
    const res = await POST();
    const body = await res.json();
    expect(body.registered).toBe(false);
    expect(body.reason).toMatch(/different webhook/);
    expect(fetchSpy).toHaveBeenCalledTimes(1);  // GET only — never a clobbering PUT
  });

  it("returns 502 when the gateway is unreachable", async () => {
    process.env.GATEWAY_URL = "http://gateway:8000";
    process.env.SALES_CYCLE_URL = "http://sales-cycle:8200";
    process.env.SALES_CYCLE_CALENDAR_WEBHOOK_SECRET = "shh";
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED"); }));

    const { POST } = await loadRoute();
    const res = await POST();
    expect(res.status).toBe(502);
    expect((await res.json()).registered).toBe(false);
  });
});
