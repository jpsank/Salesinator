import { afterEach, describe, expect, it, vi } from "vitest";
import { getSlackChannel, setOAuthToken, setSlackChannel } from "../salesCycleApi";

afterEach(() => vi.restoreAllMocks());

describe("salesCycleApi.setOAuthToken", () => {
  it("POSTs the token to /api/sales-cycle/oauth/{provider}/token", async () => {
    const fetchSpy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ connected: true, configured: false }) }) as unknown as Response);
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    const status = await setOAuthToken("hubspot", "pat-na2-secret");
    expect(status).toEqual({ connected: true, configured: false });

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sales-cycle/oauth/hubspot/token");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ token: "pat-na2-secret" });
  });

  it("throws an ApiError on a non-ok response", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 400, json: async () => ({ detail: "token must not be empty" }) }) as unknown as Response) as unknown as typeof fetch;
    await expect(setOAuthToken("hubspot", "")).rejects.toMatchObject({ status: 400 });
  });
});

describe("salesCycleApi.getSlackChannel / setSlackChannel", () => {
  it("GETs /api/sales-cycle/slack/channel with no-store", async () => {
    const fetchSpy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ channel_id: "C1", source: "env" }) }) as unknown as Response);
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    const config = await getSlackChannel();
    expect(config).toEqual({ channel_id: "C1", source: "env" });

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sales-cycle/slack/channel");
    expect(init.cache).toBe("no-store");
  });

  it("POSTs the new channel_id to /api/sales-cycle/slack/channel", async () => {
    const fetchSpy = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ channel_id: "C2", source: "override" }) }) as unknown as Response);
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    const config = await setSlackChannel("C2");
    expect(config).toEqual({ channel_id: "C2", source: "override" });

    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sales-cycle/slack/channel");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ channel_id: "C2" });
  });

  it("throws an ApiError on a non-ok response", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 400, json: async () => ({ detail: "bad" }) }) as unknown as Response) as unknown as typeof fetch;
    await expect(setSlackChannel("")).rejects.toMatchObject({ status: 400 });
  });
});
