/** Settings → Integrations, Slack card's live channel check (SlackChannelCheck).
 *
 *  This exists because OAuth status alone can't tell you the app actually works: a connected app
 *  that was never invited into the configured channel looks IDENTICAL to a working one from the
 *  OAuth status alone — reproduced live, a real feature_request card silently never reached Slack
 *  for exactly this reason. This pins the four states GET /slack/channel-status can report.
 */
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SlackChannelCheck } from "../salesCycleConnection";
import type { SlackChannelStatus } from "../salesCycleApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stub(status: SlackChannelStatus) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(status), { status: 200 })));
}

it("shows nothing configured when no channel is set", async () => {
  stub({ configured: false });
  render(<SlackChannelCheck />);
  await waitFor(() => expect(screen.getByText(/no channel configured/i)).toBeTruthy());
});

it("shows ready when connected and a member", async () => {
  stub({ configured: true, channel_id: "C1", channel_name: "feature-requests", is_member: true, error: null });
  render(<SlackChannelCheck />);
  await waitFor(() => expect(screen.getByText(/ready/i)).toBeTruthy());
  expect(screen.getByText(/feature-requests/)).toBeTruthy();
});

it("shows an actionable invite instruction when the app isn't a channel member", async () => {
  stub({ configured: true, channel_id: "C1", channel_name: "feature-requests", is_member: false, error: null });
  render(<SlackChannelCheck />);
  await waitFor(() => expect(screen.getByText(/not invited into/i)).toBeTruthy());
  expect(screen.getByText(/\/invite/)).toBeTruthy();
});

it("shows a channel_not_found reason", async () => {
  stub({ configured: true, channel_id: "C_BAD", is_member: null, error: "channel_not_found" });
  render(<SlackChannelCheck />);
  await waitFor(() => expect(screen.getByText(/doesn.t exist, or it.s private/i)).toBeTruthy());
});

it("shows a reconnect instruction for an invalid/revoked connection", async () => {
  stub({ configured: true, channel_id: "C1", is_member: null, error: "invalid_auth" });
  render(<SlackChannelCheck />);
  await waitFor(() => expect(screen.getByText(/reconnect slack/i)).toBeTruthy());
});

it("surfaces a fetch failure as an alert, not a silent blank", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("not json{{", { status: 500 })));
  render(<SlackChannelCheck />);
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
});
