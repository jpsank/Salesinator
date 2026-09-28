"use client";
/** Settings → Integrations → Sales Cycle: "Product repo" — the repo sales-cycle's approved-feature-
 *  request pipeline builds against (orchestrator.py). A Lovable-style connect: OAuth to GitHub, then
 *  pick from a dropdown of the connected account's own repos — never a clone URL or a PAT to paste.
 *  One connection made once by whoever administers this deployment, same as HubSpot/Slack next to it.
 */
import { useCallback, useEffect, useState } from "react";
import {
  cardBtn as btn, cardField as field, cardMeta as meta, cardPrimaryBtn as primaryBtn, cardRow as row,
  useOAuthRedirectFeedback,
} from "./integrationCard";
import { presentError } from "./apiClient";
import {
  attachProductRepo, getProductRepoAttached, getProductRepoTokenStatus, listProductRepoOptions,
  productRepoConnectUrl, type RepoOption,
} from "./productRepoApi";
import type { AttachedWorkspaces, SavedGitToken } from "./workspaceApi";

function RepoPicker({ onAttached }: { onAttached: (attached: AttachedWorkspaces) => void }) {
  const [repos, setRepos] = useState<RepoOption[] | null>(null);
  const [selected, setSelected] = useState("");
  const [ref, setRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let on = true;
    listProductRepoOptions()
      .then((r) => { if (!on) return; setRepos(r); if (r.length > 0) { setSelected(r[0].clone_url); setRef(r[0].default_branch); } })
      .catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, []);

  const pick = (cloneUrl: string) => {
    setSelected(cloneUrl);
    const repo = repos?.find((r) => r.clone_url === cloneUrl);
    if (repo) setRef(repo.default_branch);
  };

  const attach = async () => {
    if (!selected || busy) return;
    setBusy(true); setErr(null);
    try {
      await attachProductRepo(selected, ref || "main");
      onAttached(await getProductRepoAttached());
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  if (err) return <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ Couldn’t load your repos — {err}</div>;
  if (repos === null) return <div style={meta}>Loading your repos…</div>;
  if (repos.length === 0) return <div style={meta}>No repos found on that GitHub account.</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <select value={selected} onChange={(e) => pick(e.target.value)} style={{ ...field, flex: 2 }}>
          {repos.map((r) => <option key={r.clone_url} value={r.clone_url}>{r.full_name}{r.private ? " (private)" : ""}</option>)}
        </select>
        <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="branch" style={{ ...field, flex: 1 }} />
      </div>
      <div>
        <button disabled={busy || !selected} onClick={() => void attach()}
          style={{ ...primaryBtn, opacity: busy || !selected ? 0.5 : 1 }}>
          {busy ? "Attaching…" : "Use this repo"}
        </button>
      </div>
    </div>
  );
}

export function ProductRepoCard() {
  const [tokenStatus, setTokenStatus] = useState<SavedGitToken | null>(null);
  const [attached, setAttached] = useState<AttachedWorkspaces | null>(null);
  const [picking, setPicking] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const redirectFeedback = useOAuthRedirectFeedback("product_repo_github");

  const refresh = useCallback(() => {
    Promise.all([getProductRepoTokenStatus(), getProductRepoAttached()])
      .then(([t, a]) => { setTokenStatus(t); setAttached(a); })
      .catch((e: unknown) => setErr(presentError(e).headline));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const activeSlot = attached && attached.active ? attached.slots[attached.active] : undefined;
  const hasRepo = !!activeSlot?.repo;

  return (
    <div style={row}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--t1)" }}>Product repo</span>
        <span style={{ flex: 1, fontSize: 11.5, color: "var(--t3)" }}>
          {tokenStatus === null ? "Checking…"
            : !tokenStatus.oauth_configured ? "OAuth not registered on this deployment"
            : !tokenStatus.set ? "Not connected"
            : hasRepo ? `${activeSlot!.repo} · ${activeSlot!.ref ?? "main"}`
            : "Connected — pick a repo below"}
        </span>
        {tokenStatus?.oauth_configured && !tokenStatus.set && (
          <a href={productRepoConnectUrl} style={{ ...primaryBtn, textDecoration: "none", display: "inline-block" }}>Connect GitHub</a>
        )}
        {tokenStatus?.set && hasRepo && !picking && (
          <button onClick={() => setPicking(true)} style={btn}>Change repo</button>
        )}
      </div>
      <div style={meta}>
        Once approved (✓ in Slack), an agent implements the feature request as a branch on this repo and
        pushes it for review.
      </div>
      {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
      {tokenStatus?.set && (!hasRepo || picking) && (
        <RepoPicker onAttached={(a) => { setAttached(a); setPicking(false); }} />
      )}
      {redirectFeedback.connected && (
        <div role="status" style={{ fontSize: 11.5, color: "var(--green)" }}>✓ GitHub connected — pick a repo below.</div>
      )}
      {redirectFeedback.error && (
        <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ Connecting GitHub failed: {redirectFeedback.error}</div>
      )}
    </div>
  );
}
