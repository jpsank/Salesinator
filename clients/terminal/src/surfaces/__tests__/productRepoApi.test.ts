/** Isolation harness — the shared "product repo" GitHub connection's data-access. Every call must
 *  name the fixed ?for=product-repo subject — the whole point is acting on that ONE shared identity
 *  instead of the caller's own workspace. */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  attachProductRepo, getProductRepoAttached, getProductRepoTokenStatus, listProductRepoOptions,
  productRepoConnectUrl,
} from "../productRepoApi";

let fetchMock: ReturnType<typeof vi.fn>;
const urls: string[] = [];
function mock(body: unknown) {
  fetchMock = vi.fn(async (url: string) => { urls.push(url); return { ok: true, status: 200, json: async () => body } as unknown as Response; });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
}
afterEach(() => { vi.restoreAllMocks(); urls.length = 0; });

describe("productRepoApi — always the fixed product-repo subject", () => {
  it("productRepoConnectUrl names the shared subject", () => {
    expect(productRepoConnectUrl).toBe("/api/github/oauth/authorize?for=product-repo");
  });

  it("getProductRepoTokenStatus GETs /api/workspace/git-token?for=product-repo", async () => {
    mock({ set: true, masked: "••••abcd", oauth_configured: true });
    const s = await getProductRepoTokenStatus();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/workspace/git-token?for=product-repo");
    expect(s.set).toBe(true);
  });

  it("getProductRepoAttached GETs /api/workspace/attached?for=product-repo", async () => {
    mock({ active: "origin-1", slots: {} });
    await getProductRepoAttached();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/workspace/attached?for=product-repo");
  });

  it("listProductRepoOptions GETs the repos endpoint and unwraps { repos }", async () => {
    mock({ repos: [{ full_name: "acme/api", clone_url: "https://github.com/acme/api.git", default_branch: "main", private: true }] });
    const repos = await listProductRepoOptions();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/workspace/git-token/oauth/repos?for=product-repo");
    expect(repos).toEqual([{ full_name: "acme/api", clone_url: "https://github.com/acme/api.git", default_branch: "main", private: true }]);
  });

  it("attachProductRepo inits then swaps, both naming the shared subject", async () => {
    mock({ subject: "product-repo", active: "origin-1", repo: "https://github.com/acme/api.git", ref: "main", swapped: true, cloned: true, parked: null, nested: false });
    await attachProductRepo("https://github.com/acme/api.git", "main");
    expect(urls[0]).toBe("/api/workspace/init?for=product-repo");
    expect(urls[1]).toBe("/api/workspace/swap");
    const body = JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body));
    expect(body).toEqual({ repo: "https://github.com/acme/api.git", ref: "main", for_subject: "product-repo" });
  });
});
