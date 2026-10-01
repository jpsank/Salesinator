/** GitHubTokenCard's ProductRepoPicker (Settings → Integrations → GitHub → "Product repo") — the
 *  actual component behind a live production report: the outer connection card showed "Connected"
 *  while this nested picker stayed stuck on "⚠ The Vexa server can't reach a backend service right
 *  now." with no way to recover. Root cause confirmed via the gateway's own logs: a 502 burst on
 *  exactly the repo-list call this component makes, then nothing for 28 hours — but the component's
 *  effect ran once on mount and never retried, so the stale error outlived the underlying blip by
 *  a day. Retry must actually recover without a page reload.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

vi.mock("../workspaceApi", () => ({
  getGitToken: vi.fn(async () => ({ set: true, masked: "ghp_****", target_subject: "u_live" })),
  setGitToken: vi.fn(async () => ({ set: false, masked: null })),
  initWorkspace: vi.fn(async () => undefined),
  swapWorkspace: vi.fn(async () => undefined),
  readAttachedWorkspaces: vi.fn(async () => ({ active: null, slots: {} })),
  listMyGitHubRepos: vi.fn(),
}));

import { GitHubTokenCard } from "../tokens";
import { ApiError } from "../apiClient";
import * as workspaceApi from "../workspaceApi";

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const REPO = { full_name: "jsank/Salesinator", clone_url: "https://github.com/jsank/Salesinator.git", default_branch: "main", private: false };

describe("ProductRepoPicker", () => {
  it("retry recovers from a failed repo list without a reload, even while the outer card shows Connected", async () => {
    let calls = 0;
    vi.mocked(workspaceApi.listMyGitHubRepos).mockImplementation(async () => {
      calls += 1;
      if (calls === 1) throw new ApiError(502, "GitHub repo list failed: HTTP 502", "/api/workspace/git-token/oauth/repos");
      return [REPO];
    });

    render(<GitHubTokenCard />);

    // The outer connection status succeeds independently — "even though it's connected" is exactly
    // the reported symptom, so this must be true while the inner picker is still erroring.
    await waitFor(() => expect(screen.getByText(/Connected/)).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/can't reach a backend service/i)).toBeTruthy());
    expect(screen.queryByText("Loading your repos…")).toBeNull(); // not stuck claiming to still be loading

    fireEvent.click(screen.getByRole("button", { name: /retry/i }));

    await waitFor(() => expect(screen.getByText("jsank/Salesinator")).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(workspaceApi.listMyGitHubRepos).toHaveBeenCalledTimes(2);
  });

  it("a clean first load lists repos with no error and no Retry button", async () => {
    vi.mocked(workspaceApi.listMyGitHubRepos).mockResolvedValue([REPO]);
    render(<GitHubTokenCard />);
    await waitFor(() => expect(screen.getByText("jsank/Salesinator")).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: /retry/i })).toBeNull();
  });
});
