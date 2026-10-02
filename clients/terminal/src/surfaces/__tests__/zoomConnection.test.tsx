/** The Zoom card: "Connected" alone proves nothing, so it shows what the connection has done and what would stop it. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { OAuthConnectionCard } from "../integrationCard";
import { ZoomActivity, type ZoomStatus } from "../zoomConnection";

afterEach(() => cleanup());

const base: ZoomStatus = { connected: true, configured: true, webhook_ready: true, account_label: "rep@acme.com" };

describe("ZoomActivity", () => {
  it("says so when no meeting has happened yet", () => {
    render(<ZoomActivity status={{ ...base, last_join: null }} />);
    expect(screen.getByText(/No meeting yet/)).toBeTruthy();
  });

  it("names the last meeting and what happened to it", () => {
    render(<ZoomActivity status={{ ...base, last_join: { topic: "Acme demo", started_at: 1, outcome: "joined" } }} />);
    expect(screen.getByText("Acme demo")).toBeTruthy();
    expect(screen.getByText(/the bot joined/)).toBeTruthy();
  });

  it("explains a failure with its reason, and a rejected key with the fix", () => {
    const { rerender } = render(<ZoomActivity status={{ ...base, last_join: { topic: "x", started_at: 1, outcome: "failed", detail: "Vexa answered 500" } }} />);
    expect(screen.getByText(/couldn't join/)).toBeTruthy();
    expect(screen.getByText(/Vexa answered 500/)).toBeTruthy();
    rerender(<ZoomActivity status={{ ...base, last_join: { topic: "x", started_at: 1, outcome: "vexa_key_rejected" } }} />);
    expect(screen.getByText(/disconnect and reconnect Zoom/)).toBeTruthy();
  });

  it("warns that nothing will join when the deployment has no webhook secret", () => {
    render(<ZoomActivity status={{ ...base, webhook_ready: false }} />);
    expect(screen.getByRole("alert").textContent).toContain("SALES_CYCLE_ZOOM_WEBHOOK_SECRET_TOKEN");
  });
});

describe("the Zoom card shape", () => {
  it("shows its activity only once connected, and 'OAuth not registered' when the deployment has no Zoom app", async () => {
    const { unmount } = render(
      <OAuthConnectionCard provider="zoom" label="Zoom" description="d" connectUrl="/api/zoom/authorize"
        getStatus={async () => ({ connected: false, configured: false })} disconnect={async () => ({ connected: false })}
        extra={() => <div>activity</div>} />);
    await waitFor(() => expect(screen.getByText(/OAuth not registered/)).toBeTruthy());
    expect(screen.queryByText("activity")).toBeNull();
    unmount();
    render(
      <OAuthConnectionCard provider="zoom" label="Zoom" description="d" connectUrl="/api/zoom/authorize"
        getStatus={async () => base} disconnect={async () => ({ connected: false })} extra={() => <div>activity</div>} />);
    await waitFor(() => expect(screen.getByText("activity")).toBeTruthy());
  });
});
