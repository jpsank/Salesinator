/** Settings → Integrations, Slack card's live channel check (SlackChannelCheck) and the channel ID
 *  field that sets what it checks (SlackChannelField).
 *
 *  SlackChannelCheck exists because OAuth status alone can't tell you the app actually works: a
 *  connected app that was never invited into the configured channel looks IDENTICAL to a working
 *  one from the OAuth status alone — reproduced live, a real feature_request card silently never
 *  reached Slack for exactly this reason. This pins the four states GET /slack/channel-status can
 *  report.
 *
 *  SlackChannelField exists because, before it, the ONLY way to change the channel was
 *  SALES_CYCLE_SLACK_CHANNEL_ID + a restart — this pins its load/save/clear roundtrip against
 *  GET/POST /slack/channel.
 */
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SlackChannelCheck, SlackChannelField } from "../salesCycleConnection";
import type { SlackChannelConfig, SlackChannelStatus } from "../salesCycleApi";

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

// ---- SlackChannelField — the "change it here, not an env var" control ---------------------------

function stubSequence(responses: SlackChannelConfig[]) {
  let i = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    const body = responses[Math.min(i, responses.length - 1)];
    i += 1;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

it("prefills with the effective value and labels an env default as such", async () => {
  stubSequence([{ channel_id: "C_ENV_DEFAULT", source: "env" }]);
  render(<SlackChannelField />);
  const input = (await screen.findByPlaceholderText("C0123ABCDEF")) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("C_ENV_DEFAULT"));
  expect(screen.getByText(/SALES_CYCLE_SLACK_CHANNEL_ID/)).toBeTruthy();
});

it("saving a typed channel id persists it and notifies the caller", async () => {
  stubSequence([
    { channel_id: null, source: "unset" },
    { channel_id: "C_FROM_UI", source: "override" },
  ]);
  const onSaved = vi.fn();
  render(<SlackChannelField onSaved={onSaved} />);
  const input = (await screen.findByPlaceholderText("C0123ABCDEF")) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe(""));
  fireEvent.change(input, { target: { value: "C_FROM_UI" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(screen.getByText(/^saved$/i)).toBeTruthy());
  expect(input.value).toBe("C_FROM_UI");
  expect(onSaved).toHaveBeenCalledTimes(1);
});

it("saving an emptied field reverts to the env default (clear, not a blank override)", async () => {
  stubSequence([
    { channel_id: "C_FROM_UI", source: "override" },
    { channel_id: "C_ENV_DEFAULT", source: "env" },
  ]);
  render(<SlackChannelField />);
  const input = (await screen.findByPlaceholderText("C0123ABCDEF")) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("C_FROM_UI"));
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(input.value).toBe("C_ENV_DEFAULT"));
});

it("surfaces a save failure as an alert", async () => {
  let call = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    call += 1;
    if (call === 1) return new Response(JSON.stringify({ channel_id: "C1", source: "env" }), { status: 200 });
    return new Response(JSON.stringify({ detail: "boom" }), { status: 500 });
  }));
  render(<SlackChannelField />);
  const input = (await screen.findByPlaceholderText("C0123ABCDEF")) as HTMLInputElement;
  await waitFor(() => expect(input.value).toBe("C1"));
  fireEvent.change(input, { target: { value: "C2" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
});

it("the save button stays disabled until the field is actually changed", async () => {
  stubSequence([{ channel_id: "C1", source: "env" }]);
  render(<SlackChannelField />);
  await screen.findByPlaceholderText("C0123ABCDEF");
  const button = screen.getByRole("button", { name: /save/i }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
});
