/** The Vexa Capture card: pairing starts where the person already is, and each paired Mac can be disconnected. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { CaptureCard, lastUsed } from "../captureConnection";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const NOW = Date.parse("2026-10-02T12:00:00Z");
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

describe("lastUsed", () => {
  it("says it plainly", () => {
    expect(lastUsed(null, NOW)).toBe("not used yet");
    expect(lastUsed("2026-10-02T11:59:50Z", NOW)).toBe("just now");
    expect(lastUsed("2026-10-02T11:55:00Z", NOW)).toBe("5 min ago");
    expect(lastUsed("2026-10-02T09:00:00Z", NOW)).toBe("3 h ago");
    expect(lastUsed("2026-09-29T12:00:00Z", NOW)).toBe("3 days ago");
    expect(lastUsed("2026-10-02T11:00:00", NOW)).toBe("1 h ago");           // a timestamp with no zone is UTC
    expect(lastUsed("garbage", NOW)).toBe("not used yet");
  });
});

describe("the card", () => {
  let hrefs: string[] = [];
  beforeEach(() => {
    hrefs = [];
    Object.defineProperty(window, "location", { configurable: true, value: { set href(v: string) { hrefs.push(v); }, get href() { return "http://localhost/"; } } });
  });

  it("with no Mac connected, offers Connect a Mac", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ok({ devices: [] })));
    render(<CaptureCard now={() => NOW} />);
    await waitFor(() => expect(screen.getByText("No Mac connected")).toBeTruthy());
    expect(screen.getByRole("button", { name: "Connect a Mac" })).toBeTruthy();
  });

  it("offers Download for Mac when the deployment publishes a build, and not otherwise", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ok({ devices: [], download: "https://example.com/VexaCapture.dmg" })));
    const { unmount } = render(<CaptureCard now={() => NOW} />);
    const link = await waitFor(() => screen.getByRole("link", { name: "Download for Mac" }));
    expect(link.getAttribute("href")).toBe("https://example.com/VexaCapture.dmg");
    unmount();
    vi.stubGlobal("fetch", vi.fn(async () => ok({ devices: [], download: null })));
    render(<CaptureCard now={() => NOW} />);
    await waitFor(() => screen.getByText("No Mac connected"));
    expect(screen.queryByRole("link", { name: "Download for Mac" })).toBeNull();
  });

  it("Connect asks the server for a code and opens the app with it, staying on the page", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) =>
      url.includes("/pair") && init?.method === "POST" ? ok({ link: "vexacapture://connect?code=abc&base=https%3A%2F%2Fsite" }) : ok({ devices: [] }));
    vi.stubGlobal("fetch", fetchMock);
    render(<CaptureCard now={() => NOW} />);
    await waitFor(() => screen.getByRole("button", { name: "Connect a Mac" }));
    fireEvent.click(screen.getByRole("button", { name: "Connect a Mac" }));
    await waitFor(() => expect(hrefs).toEqual(["vexacapture://connect?code=abc&base=https%3A%2F%2Fsite"]));
    expect(screen.getByRole("status").textContent).toContain("install.sh");
  });

  it("names each Mac, says when it connected and was last used, and asks before disconnecting one", async () => {
    let devices = [
      { id: 7, name: "Julian's MacBook Pro", created_at: "2026-09-29T12:00:00Z", last_used_at: "2026-10-02T11:55:00Z" },
      { id: 8, name: null, created_at: null, last_used_at: null },
    ];
    const revoked: number[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/revoke")) { const id = JSON.parse(init!.body as string).id; revoked.push(id); devices = devices.filter((d) => d.id !== id); return ok({ ok: true }); }
      return ok({ devices });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<CaptureCard now={() => NOW} />);
    await waitFor(() => expect(screen.getByText("2 Macs connected")).toBeTruthy());
    expect(screen.getByText(/Julian's MacBook Pro · connected 3 days ago · last used 5 min ago/)).toBeTruthy();
    expect(screen.getByText(/Mac \(older connection\) · last used not used yet/)).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: "Disconnect" })[1]);        // asks first; nothing is revoked yet
    expect(screen.getByText(/stop working until you connect it again/)).toBeTruthy();
    expect(revoked).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText(/stop working until/)).toBeNull();

    fireEvent.click(screen.getAllByRole("button", { name: "Disconnect" })[1]);
    fireEvent.click(screen.getByRole("button", { name: "Yes, disconnect" }));
    await waitFor(() => expect(screen.getByText("1 Mac connected")).toBeTruthy());
    expect(revoked).toEqual([8]);
    expect(screen.getByRole("button", { name: "Connect another Mac" })).toBeTruthy();
  });

  it("shows a failure instead of hanging", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "nope" }), { status: 401 })));
    render(<CaptureCard now={() => NOW} />);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});
