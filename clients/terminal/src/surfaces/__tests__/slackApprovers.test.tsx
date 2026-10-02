/** Settings → Integrations → Slack → "Who can approve": which Slack people may give the go-ahead on a feature request (a leader's ✅
 *  approves it once 👍 outnumber 👎). Pins the client calls, the warning while nobody is chosen (anyone's ✅ still approves), and the
 *  load / save / clear round trip against GET/POST /slack/approvers. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { SlackApproversField } from "../salesCycleConnection";
import { getSlackApprovers, setSlackApprovers, type SlackApprovers } from "../salesCycleApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const none: SlackApprovers = { user_ids: [], include_admins: false, usergroup_ids: [], configured: false };

describe("the approvers client", () => {
  it("GETs /api/sales-cycle/slack/approvers with no-store", async () => {
    const fetchSpy = vi.fn(async () => json(none));
    vi.stubGlobal("fetch", fetchSpy);
    expect(await getSlackApprovers()).toEqual(none);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sales-cycle/slack/approvers");
    expect(init.cache).toBe("no-store");
  });

  it("POSTs the three sources", async () => {
    const fetchSpy = vi.fn(async () => json({ ...none, user_ids: ["UA"], configured: true }));
    vi.stubGlobal("fetch", fetchSpy);
    await setSlackApprovers({ user_ids: ["UA"], include_admins: true, usergroup_ids: ["SG"] });
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/sales-cycle/slack/approvers");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ user_ids: ["UA"], include_admins: true, usergroup_ids: ["SG"] });
  });
});

describe("the Who can approve field", () => {
  it("warns that anyone can approve while nobody is chosen", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(none)));
    render(<SlackApproversField />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("anyone in the channel can approve"));
  });

  it("shows the voting rule once leaders are chosen, with the saved values filled in", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ user_ids: ["UA", "UB"], include_admins: true, usergroup_ids: ["SG1"], configured: true })));
    render(<SlackApproversField />);
    await waitFor(() => expect(screen.getByText(/outnumber/)).toBeTruthy());
    expect((screen.getByPlaceholderText("U0123ABCD, U0456EFGH") as HTMLInputElement).value).toBe("UA, UB");
    expect((screen.getByPlaceholderText("S0123ABCD") as HTMLInputElement).value).toBe("SG1");
    expect((screen.getByLabelText("Workspace admins and owners can approve") as HTMLInputElement).checked).toBe(true);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("saves what was typed, splitting ids on commas, spaces and new lines", async () => {
    const calls: Array<[string, RequestInit | undefined]> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return init?.method === "POST" ? json({ user_ids: ["UA", "UB"], include_admins: true, usergroup_ids: [], configured: true }) : json(none);
    }));
    render(<SlackApproversField />);
    await waitFor(() => screen.getByRole("status"));
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);      // nothing changed yet
    fireEvent.change(screen.getByPlaceholderText("U0123ABCD, U0456EFGH"), { target: { value: "UA,\nUB  " } });
    fireEvent.click(screen.getByLabelText("Workspace admins and owners can approve"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Saved")).toBeTruthy());
    const post = calls.find(([, i]) => i?.method === "POST")!;
    expect(JSON.parse(post[1]!.body as string)).toEqual({ user_ids: ["UA", "UB"], include_admins: true, usergroup_ids: [] });
    expect(screen.queryByRole("status")).toBeNull();            // the warning is gone: leaders are chosen now
  });

  it("shows why an id was refused instead of pretending it saved", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_u: string, init?: RequestInit) =>
      init?.method === "POST" ? json({ detail: "not a Slack member id (U…) or user group id (S…): alice" }, 422) : json(none)));
    render(<SlackApproversField />);
    await waitFor(() => screen.getByRole("status"));
    fireEvent.change(screen.getByPlaceholderText("U0123ABCD, U0456EFGH"), { target: { value: "alice" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.queryByText("Saved")).toBeNull();
  });
});
