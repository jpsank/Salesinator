// Turns a verified email into that person's own Vexa API key, so a preview shows exactly what they could already see.
// Looks the person up — never creates an account — and mints a short-lived key that the gate alone holds.

const REFRESH_MARGIN_MS = 5 * 60 * 1000;

export function createViewerKeys({ adminUrl, adminKey, ttlSeconds = 6 * 60 * 60, scopes = "bot,tx,browser", fetchImpl = fetch, now = () => Date.now() }) {
  const cache = new Map();
  const admin = (path, init = {}) =>
    fetchImpl(`${adminUrl}${path}`, {
      ...init,
      headers: { "X-Admin-API-Key": adminKey, ...init.headers },
      signal: AbortSignal.timeout(10000),
    });

  async function mint(email) {
    const found = await admin(`/admin/users/email/${encodeURIComponent(email)}`);
    if (found.status === 404) return { status: "no_account" };
    if (!found.ok) return { status: "unavailable" };
    const user = await found.json();
    const q = new URLSearchParams({ scopes, name: "preview-viewer", expires_in: String(ttlSeconds) });
    const minted = await admin(`/admin/users/${encodeURIComponent(String(user.id))}/tokens?${q}`, { method: "POST" });
    if (!minted.ok) return { status: "unavailable" };
    const { token } = await minted.json();
    if (!token) return { status: "unavailable" };
    return { status: "ok", key: token, userId: user.id, email, expires: now() + ttlSeconds * 1000 };
  }

  /** {status: "ok", key, userId, email} | {status: "no_account"} | {status: "unavailable"} */
  return async function viewerKey(email) {
    const hit = cache.get(email);
    if (hit && hit.expires - now() > REFRESH_MARGIN_MS) return hit;
    let result;
    try {
      result = await mint(email);
    } catch {
      return { status: "unavailable" };
    }
    if (result.status === "ok") cache.set(email, result);
    return result;
  };
}
