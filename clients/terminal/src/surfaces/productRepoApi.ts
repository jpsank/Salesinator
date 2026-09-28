"use client";
/** productRepoApi — the shared "product repo" GitHub connection sales-cycle's build/push pipeline
 *  runs against (orchestrator.py's `product_repo_subject`, default "product-repo"). One connection
 *  for the whole team, made once by whoever administers this deployment — like HubSpot/Slack next
 *  to it, not a per-rep setting.
 *
 *  Reuses the SAME agent-api primitives any Vexa user's own "attach a custom git repo" already
 *  uses (git-token OAuth, workspace/init, workspace/swap) via their `?for=`/`for_subject`
 *  allowlisted-target-subject support — see core/agent/control_plane/api.py's `target_subject_of`.
 *  This file just always names that one fixed subject.
 */
import { getJson } from "./apiClient";
import type { AttachedWorkspaces, SavedGitToken, SwapResult } from "./workspaceApi";

const FOR = "product-repo";  // must match the deployment's VEXA_GITHUB_OAUTH_TARGET_SUBJECT

export const productRepoConnectUrl = `/api/github/oauth/authorize?for=${FOR}`;

export interface RepoOption { full_name: string; clone_url: string; default_branch: string; private: boolean }

/** Whether GitHub is connected for the product repo (and whether OAuth is even registered) —
 *  the SAME shape a personal GitHubTokenCard reads, just for the shared subject instead of the
 *  caller's own. */
export async function getProductRepoTokenStatus(): Promise<SavedGitToken> {
  return getJson(`/api/workspace/git-token?for=${FOR}`);
}

/** Which repo (if any) is currently attached as the product repo's workspace. */
export async function getProductRepoAttached(): Promise<AttachedWorkspaces> {
  return getJson(`/api/workspace/attached?for=${FOR}`);
}

/** The connected account's own repos — what the picker offers instead of typing a clone URL. Only
 *  callable once GitHub is connected (409 otherwise — the card only calls this once it is). */
export async function listProductRepoOptions(): Promise<RepoOption[]> {
  const data = await getJson<{ repos: RepoOption[] }>(`/api/workspace/git-token/oauth/repos?for=${FOR}`);
  return data.repos;
}

/** Attach the picked repo as the product repo's workspace. Ensures the subject is seeded first
 *  (idempotent, safe every time) — a never-initialized subject nests the clone under kg/ instead
 *  of replacing the workspace root. */
export async function attachProductRepo(repo: string, ref: string): Promise<SwapResult> {
  await getJson(`/api/workspace/init?for=${FOR}`, { method: "POST" });
  return getJson(`/api/workspace/swap`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo, ref, for_subject: FOR }),
  });
}
