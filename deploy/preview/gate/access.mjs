// Verifies the signed identity Cloudflare Access attaches to every request it lets through
// (header `Cf-Access-Jwt-Assertion`, or the `CF_Authorization` cookie). The gate trusts the email in it
// only after the signature, issuer, audience and expiry all check out.
import { createPublicKey, verify } from "node:crypto";

const KEY_TTL_MS = 60 * 60 * 1000;

export function createAccessVerifier({ team, audience, fetchJson = defaultFetchJson, now = () => Date.now() }) {
  const issuer = `https://${team}.cloudflareaccess.com`;
  let keys = new Map();
  let fetchedAt = 0;

  async function load() {
    const body = await fetchJson(`${issuer}/cdn-cgi/access/certs`);
    keys = new Map((body.keys || []).map((jwk) => [jwk.kid, createPublicKey({ key: jwk, format: "jwk" })]));
    fetchedAt = now();
  }

  async function keyFor(kid) {
    if (!keys.has(kid) || now() - fetchedAt > KEY_TTL_MS) await load();
    return keys.get(kid) || null;
  }

  /** The verified email, or null. Never throws on a bad token. */
  return async function verifyAccessToken(token) {
    try {
      const [h, p, s] = String(token || "").split(".");
      if (!h || !p || !s) return null;
      const header = JSON.parse(Buffer.from(h, "base64url").toString());
      if (header.alg !== "RS256") return null;
      const key = await keyFor(header.kid);
      if (!key || !verify("RSA-SHA256", Buffer.from(`${h}.${p}`), key, Buffer.from(s, "base64url"))) return null;
      const claims = JSON.parse(Buffer.from(p, "base64url").toString());
      const aud = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
      const t = Math.floor(now() / 1000);
      if (claims.iss !== issuer || !aud.includes(audience) || !(claims.exp > t) || (claims.nbf && claims.nbf > t)) return null;
      return typeof claims.email === "string" && claims.email ? claims.email.toLowerCase() : null;
    } catch {
      return null;
    }
  };
}

async function defaultFetchJson(url) {
  const res = await fetch(url, { signal: AbortSignal.timeout(8000) });
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return res.json();
}
