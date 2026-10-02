/** Settings → Integrations → Slack → "Slack events": is Slack actually sending events? Reactions (votes, ✅) do nothing when it is not, and
 *  nothing else says so. The last event time is only a hint; "Check that events arrive" proves it end to end and says which way it failed. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SlackEventsHealth, agoFrom } from "../salesCycleConnection";
import { checkSlackEvents, getSlackEventsStatus } from "../salesCycleApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const NOW = 1_800_000_000_000;                       // ms
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("agoFrom", () => {
  it("says it plainly", () => {
    const s = NOW / 1000;
    expect([s - 3, s - 30, s - 300, s - 7200, s - 3 * 86400].map((t) => agoFrom(t, NOW))).toEqual(["just now", "30 s ago", "5 min ago", "2 h ago", "3 days ago"]);
  });
});

describe("the events client", () => {
  it("GETs the status with no-store, and POSTs the check", async () => {
    const fetchSpy = vi.fn(async () => json({ last_event_at: null, last_rejected_at: null }));
    vi.stubGlobal("fetch", fetchSpy);
    await getSlackEventsStatus();
    await checkSlackEvents();
    const [[u1, i1], [u2, i2]] = fetchSpy.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(u1).toBe("/api/sales-cycle/slack/events-status"); expect(i1.cache).toBe("no-store");
    expect(u2).toBe("/api/sales-cycle/slack/events-check"); expect(i2.method).toBe("POST");
  });
});

describe("the Slack events row", () => {
  it("says no event has arrived yet when none has", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ last_event_at: null, last_rejected_at: null })));
    render(<SlackEventsHealth now={() => NOW} />);
    await waitFor(() => expect(screen.getByText(/No event has reached Vexa yet/)).toBeTruthy());
  });

  it("shows when the last event arrived and when a request from Slack was refused", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ last_event_at: NOW / 1000 - 300, last_rejected_at: NOW / 1000 - 7200 })));
    render(<SlackEventsHealth now={() => NOW} />);
    await waitFor(() => expect(screen.getByText(/Last event from Slack: 5 min ago/)).toBeTruthy());
    expect(screen.getByText(/refused 2 h ago/)).toBeTruthy();
  });

  it("runs the check and reports that events are arriving", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return url.endsWith("events-check")
        ? json({ delivered: true, refused: false, target: "your latest feature-request card", detail: "Slack delivered the test reaction in 0.8s — events are arriving." })
        : json({ last_event_at: NOW / 1000 - 1, last_rejected_at: null });
    }));
    render(<SlackEventsHealth now={() => NOW} />);
    await waitFor(() => screen.getByRole("button", { name: "Check that events arrive" }));
    fireEvent.click(screen.getByRole("button", { name: "Check that events arrive" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("events are arriving"));
    expect(calls).toContain("POST /api/sales-cycle/slack/events-check");
    await waitFor(() => expect(screen.getByText(/Last event from Slack: just now/)).toBeTruthy());     // the status is refreshed after the check
  });

  it("reports a failed check as a warning with what to look at", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => url.endsWith("events-check")
      ? json({ delivered: false, refused: false, detail: "Nothing arrived in 10s — Slack is not sending events here. Socket Mode is OFF …" })
      : json({ last_event_at: null, last_rejected_at: null })));
    render(<SlackEventsHealth now={() => NOW} />);
    await waitFor(() => screen.getByRole("button", { name: "Check that events arrive" }));
    fireEvent.click(screen.getByRole("button", { name: "Check that events arrive" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Nothing arrived"));
    expect(screen.getByRole("alert").textContent).toContain("⚠");
  });

  it("disables the button while checking", async () => {
    let release: (r: Response) => void = () => undefined;
    vi.stubGlobal("fetch", vi.fn((url: string) => url.endsWith("events-check") ? new Promise<Response>((res) => { release = res; }) : Promise.resolve(json({ last_event_at: null, last_rejected_at: null }))));
    render(<SlackEventsHealth now={() => NOW} />);
    await waitFor(() => screen.getByRole("button", { name: "Check that events arrive" }));
    fireEvent.click(screen.getByRole("button", { name: "Check that events arrive" }));
    const busy = await waitFor(() => screen.getByRole("button", { name: /Checking/ }) as HTMLButtonElement);
    expect(busy.disabled).toBe(true);
    release(json({ delivered: true, refused: false, detail: "ok" }));
    await waitFor(() => screen.getByRole("status"));
  });
});
