/** OAuthConnectionCard — the shared Settings → Integrations card shape (Calendar, GitHub, HubSpot,
 *  Slack). Pinning the one behavior a refactor would quietly lose: a failed status check used to
 *  leave the card stuck on its error banner forever (the check ran once on mount, never again) —
 *  reproduced live via the gateway's own logs (a 502 burst, then nothing for 28 hours, the card
 *  still showing the same stale error). Retry must actually recover without a page reload.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { OAuthConnectionCard, type OAuthStatus } from "../integrationCard";
import { ApiError } from "../apiClient";

afterEach(() => cleanup());

function renderCard(getStatus: () => Promise<OAuthStatus>) {
  return render(
    <OAuthConnectionCard
      provider="github" label="GitHub" description="desc" connectUrl="/api/github/oauth/authorize"
      getStatus={getStatus} disconnect={async () => ({ connected: false })}
    />
  );
}

describe("OAuthConnectionCard", () => {
  it("retry recovers from a failed status check without a reload", async () => {
    let calls = 0;
    const getStatus = vi.fn(async (): Promise<OAuthStatus> => {
      calls += 1;
      if (calls === 1) throw new ApiError(502, "GitHub repo list failed", "/api/workspace/git-token");
      return { connected: true, account_label: "octocat" };
    });

    renderCard(getStatus);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/can't reach a backend service/i)).toBeTruthy();
    // Unknown state while erroring: neither Connect nor Disconnect is a correct guess.
    expect(screen.queryByText("Connect")).toBeNull();
    expect(screen.queryByText("Disconnect")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(screen.getByText(/Connected · octocat/)).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(getStatus).toHaveBeenCalledTimes(2);
  });

  it("a second failed retry keeps the banner (and button) up, not a silent retry loop", async () => {
    const getStatus = vi.fn(async (): Promise<OAuthStatus> => {
      throw new ApiError(504, "timeout", "/api/workspace/git-token");
    });

    renderCard(getStatus);

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    await waitFor(() => expect(getStatus).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy();
  });

  it("a clean first load shows no error and no Retry button", async () => {
    renderCard(async () => ({ connected: false }));
    await waitFor(() => expect(screen.getByText("Not connected")).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });
});
