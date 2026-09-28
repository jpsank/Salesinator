"use client";
/** API tokens + GitHub token panels — the user's credential self-serve (list, mint, revoke; save-once
 *  PAT). These render inside the SETTINGS tab (surfaces/settings.tsx — the footer-gear surface,
 *  design-spec meeting-lifecycle-v2 W5); the old "API Tokens" activity-bar item is retired so
 *  integrator config leaves the daily-driver nav. All data flows through /api/tokens, which resolves
 *  the user server-side from the auth cookies — no user_id ever leaves this component (P20). The
 *  minted token value is shown ONCE (copy it now); it is never listed again.
 */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { cardBtn, cardField, cardMeta, cardPrimaryBtn, OAuthConnectionCard, PasteTokenFallback, type OAuthStatus } from "./integrationCard";
import { listTokens, createToken, revokeToken, TOKEN_SCOPES, type TokenInfo, type TokenScope, type MintedToken } from "./tokensApi";
import {
  getGitToken, setGitToken, initWorkspace, readAttachedWorkspaces, swapWorkspace, listMyGitHubRepos,
  type AttachedWorkspaces, type GitHubRepoOption,
} from "./workspaceApi";
import { presentError } from "./apiClient";

const toOAuthStatus = (s: { set: boolean; masked: string | null; oauth_configured?: boolean }): OAuthStatus =>
  ({ connected: s.set, account_label: s.masked ?? undefined, configured: s.oauth_configured });

const EXPIRIES: Array<{ label: string; seconds?: number }> = [
  { label: "never expires" },
  { label: "1 hour", seconds: 3600 },
  { label: "24 hours", seconds: 86400 },
  { label: "30 days", seconds: 30 * 86400 },
  { label: "90 days", seconds: 90 * 86400 },
];

const fmtDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString() : null);

// Scopes speak CAPABILITIES to the user (the raw scope id rides in the tooltip + the API).
const SCOPE_LABELS: Record<string, string> = { bot: "Join meetings", tx: "Read transcripts", browser: "Browse web" };
const scopeLabel = (s: string) => SCOPE_LABELS[s] ?? s;

function TokenRow({ token, onRevoke }: { token: TokenInfo; onRevoke: (id: number) => void }) {
  const [confirming, setConfirming] = useState(false);
  const created = fmtDate(token.created_at);
  const expires = fmtDate(token.expires_at);
  return (
    <div style={{ padding: "7px 9px", borderRadius: 6, display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, color: "var(--t2)" }}>
      <Icon name="key" size={13} />
      <div style={{ minWidth: 0, flex: 1, lineHeight: 1.3 }}>
        <div style={{ color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {token.name || `token #${token.id}`}
        </div>
        <div style={{ fontSize: 11, color: "var(--t3)" }}>
          {token.scopes.map(scopeLabel).join(" · ")}{created ? ` · created ${created}` : ""}{expires ? ` · expires ${expires}` : ""}
        </div>
      </div>
      {confirming ? (
        <>
          <button onClick={() => onRevoke(token.id)} style={{ background: "none", border: "none", color: "var(--danger)", cursor: "pointer", fontSize: 11.5, padding: 2 }}>revoke</button>
          <button onClick={() => setConfirming(false)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 11.5, padding: 2 }}>keep</button>
        </>
      ) : (
        <button title="Revoke token" onClick={() => setConfirming(true)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 2 }}>
          <Icon name="x" size={13} />
        </button>
      )}
    </div>
  );
}

/** The one-time reveal: shown right after a mint, then gone forever (the list never carries the value). */
function MintedTokenCard({ minted, onDismiss }: { minted: MintedToken; onDismiss: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = () => { copyText(minted.token); setCopied(true); };
  return (
    <div style={{ margin: "8px 4px", padding: 10, borderRadius: 8, border: "1px solid var(--line)", background: "var(--panel2)" }}>
      <div style={{ fontSize: 11.5, color: "var(--t2)", marginBottom: 6 }}>
        Token created — copy it now, it will <b>not</b> be shown again.
      </div>
      <code style={{ display: "block", fontSize: 11, color: "var(--t1)", wordBreak: "break-all", marginBottom: 8 }}>{minted.token}</code>
      <div style={{ display: "flex", gap: 8 }}>
        <button onClick={copy} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11.5, padding: "3px 8px", borderRadius: 6, border: "1px solid var(--line)", background: "transparent", color: "var(--t1)", cursor: "pointer" }}>
          <Icon name="copy" size={12} />{copied ? "copied" : "copy"}
        </button>
        <button onClick={onDismiss} style={{ fontSize: 11.5, padding: "3px 8px", borderRadius: 6, border: "none", background: "transparent", color: "var(--t3)", cursor: "pointer" }}>done</button>
      </div>
    </div>
  );
}

function CreateTokenForm({ onCreated }: { onCreated: (t: MintedToken) => void }) {
  const [scopes, setScopes] = useState<TokenScope[]>(["bot", "tx", "browser"]);
  const [name, setName] = useState("");
  const [expiryIdx, setExpiryIdx] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (s: TokenScope) =>
    setScopes((prev) => (prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]));

  const submit = async () => {
    if (scopes.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const minted = await createToken({ scopes, name: name.trim() || undefined, expiresIn: EXPIRIES[expiryIdx].seconds });
      setName("");
      onCreated(minted);
    } catch (e: unknown) {
      setError(presentError(e).headline);  // fail-loud (P18)
    } finally {
      setBusy(false);
    }
  };

  const field = { width: "100%", fontSize: 12, padding: "5px 8px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)" } as const;
  return (
    <div style={{ margin: "4px 4px 10px", padding: 10, borderRadius: 8, border: "1px solid var(--line)" }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" style={{ ...field, marginBottom: 8 }} />
      <div style={{ display: "flex", gap: 10, marginBottom: 8 }}>
        {TOKEN_SCOPES.map((s) => (
          <label key={s} title={`scope: ${s}`} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--t2)", cursor: "pointer" }}>
            <input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} />{scopeLabel(s)}
          </label>
        ))}
      </div>
      <select value={expiryIdx} onChange={(e) => setExpiryIdx(Number(e.target.value))} style={{ ...field, marginBottom: 8 }}>
        {EXPIRIES.map((e, i) => <option key={e.label} value={i}>{e.label}</option>)}
      </select>
      {error && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", marginBottom: 8 }}>⚠ {error}</div>}
      <button onClick={() => void submit()} disabled={busy || scopes.length === 0}
        style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)", cursor: busy || scopes.length === 0 ? "default" : "pointer", opacity: busy || scopes.length === 0 ? 0.6 : 1 }}>
        <Icon name="plus" size={12} />{busy ? "creating…" : "Create token"}
      </button>
    </div>
  );
}

/** The product-repo picker — GitHub's connected-only "extra" step (rendered inside the SAME card,
 *  only once GitHub is connected above — no separate "Connect GitHub" for this). Reuses whichever
 *  token just got connected: lists the admin's OWN repos, and attaching one clones it under the
 *  shared "product repo" identity's workspace (`target_subject`, read from GET /api/workspace/
 *  git-token — never hardcoded here) using that SAME token. A copy of the token is persisted under
 *  that identity server-side too (see ws_swap), so the eventual push — which runs AS that shared
 *  identity, not as whoever set this up — can still authenticate on its own. */
function ProductRepoPicker() {
  const [targetSubject, setTargetSubject] = useState<string | null>(null);
  const [attached, setAttached] = useState<AttachedWorkspaces | null>(null);
  const [repos, setRepos] = useState<GitHubRepoOption[] | null>(null);
  const [selected, setSelected] = useState("");
  const [ref, setRef] = useState("");
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let on = true;
    getGitToken().then((t) => on && setTargetSubject(t.target_subject || null)).catch(() => undefined);
    return () => { on = false; };
  }, []);

  useEffect(() => {
    if (!targetSubject) return;
    let on = true;
    readAttachedWorkspaces(targetSubject).then((a) => on && setAttached(a)).catch((e: unknown) => on && setErr(presentError(e).headline));
    return () => { on = false; };
  }, [targetSubject]);

  useEffect(() => {
    let on = true;
    listMyGitHubRepos()
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
    if (!selected || busy || !targetSubject) return;
    setBusy(true); setErr(null);
    try {
      await initWorkspace(targetSubject);
      await swapWorkspace(selected, ref || "main", undefined, false, undefined, targetSubject);
      setAttached(await readAttachedWorkspaces(targetSubject));
      setPicking(false);
    } catch (e: unknown) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const activeSlot = attached?.active ? attached.slots[attached.active] : undefined;
  const hasRepo = !!activeSlot?.repo;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px dashed var(--line)", paddingTop: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--t1)" }}>Product repo</span>
        <span style={{ flex: 1, fontSize: 11.5, color: "var(--t3)" }}>
          {attached === null ? "Checking…" : hasRepo ? `${activeSlot!.repo} · ${activeSlot!.ref ?? "main"}` : "Not set"}
        </span>
        {hasRepo && !picking && <button onClick={() => setPicking(true)} style={cardBtn}>Change</button>}
      </div>
      <div style={cardMeta}>
        Once a feature request is approved (✓ in Slack), an agent implements it as a branch on this
        repo and pushes it for review.
      </div>
      {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
      {(!hasRepo || picking) && (
        repos === null ? <div style={cardMeta}>Loading your repos…</div>
        : repos.length === 0 ? <div style={cardMeta}>No repos found on this GitHub account.</div>
        : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", gap: 8 }}>
              <select value={selected} onChange={(e) => pick(e.target.value)} style={{ ...cardField, flex: 2 }}>
                {repos.map((r) => <option key={r.clone_url} value={r.clone_url}>{r.full_name}{r.private ? " (private)" : ""}</option>)}
              </select>
              <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="branch" style={{ ...cardField, flex: 1 }} />
            </div>
            <div>
              <button disabled={busy || !selected} onClick={() => void attach()}
                style={{ ...cardPrimaryBtn, opacity: busy || !selected ? 0.5 : 1 }}>
                {busy ? "Attaching…" : "Use this repo"}
              </button>
            </div>
          </div>
        )
      )}
    </div>
  );
}

/** The SAVE-ONCE reusable GitHub token (git_credentials) — per-person, unlike HubSpot/Slack next to
 *  it. "Connect GitHub" (OAuth) is the default path; pasting a PAT is the fallback, both writing to
 *  the same store. Applied for push / pull / publish / attach across ALL of the user's repos — and,
 *  once connected, also for picking the shared product repo below (see ProductRepoPicker). */
export function GitHubTokenCard() {
  const getStatus = useCallback(async () => toOAuthStatus(await getGitToken()), []);
  const disconnect = useCallback(async () => toOAuthStatus(await setGitToken(null)), []);
  return (
    <OAuthConnectionCard provider="github" label="GitHub"
      description="Lets an agent push a finished feature-request branch to your product repo for review."
      connectUrl="/api/github/oauth/authorize" getStatus={getStatus} disconnect={disconnect}
      fallback={(onConnected) => (
        <PasteTokenFallback
          description="Or paste your own fine-grained PAT (revocable on GitHub anytime):"
          placeholder="ghp_…" saveToken={async (v) => toOAuthStatus(await setGitToken(v))}
          onConnected={onConnected} />
      )}
      extra={() => <ProductRepoPicker />} />
  );
}

export function TokensPanel() {
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [minted, setMinted] = useState<MintedToken | null>(null);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18)

  const refresh = useCallback(() => {
    void listTokens().then((t) => { setTokens(t); setError(null); }).catch((e: unknown) => setError(presentError(e).headline));
  }, []);
  useEffect(() => refresh(), [refresh]);

  const onCreated = (t: MintedToken) => { setMinted(t); refresh(); };
  const onRevoke = (id: number) => {
    void revokeToken(id).then(refresh).catch((e: unknown) => setError(presentError(e).headline));
  };

  return (
    <div style={{ padding: "8px" }}>
      {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "6px 9px" }}>⚠ Couldn’t load tokens — {error}</div>}
      {minted && <MintedTokenCard minted={minted} onDismiss={() => setMinted(null)} />}
      <CreateTokenForm onCreated={onCreated} />
      {tokens.map((t) => <TokenRow key={t.id} token={t} onRevoke={onRevoke} />)}
      {tokens.length === 0 && !error && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No API tokens yet.</div>}
    </div>
  );
}
