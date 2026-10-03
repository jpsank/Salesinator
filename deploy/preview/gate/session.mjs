// The opaque preview session: what the browser (and so the preview container) holds instead of a real Vexa token.
// It names a viewer and a preview, is signed by the gate, and means nothing to any service except the gate.
import { createHmac, timingSafeEqual } from "node:crypto";

const b64 = (buf) => Buffer.from(buf).toString("base64url");
const sign = (secret, body) => createHmac("sha256", secret).update(body).digest();

export function issueSession(secret, { email, preview, ttlSeconds }, now = Date.now()) {
  const body = b64(JSON.stringify({ e: email, p: preview, x: Math.floor(now / 1000) + ttlSeconds }));
  return `pv1.${body}.${b64(sign(secret, body))}`;
}

/** The viewer and preview a session names, or null when it is forged, expired, or malformed. */
export function readSession(secret, token, now = Date.now()) {
  const parts = typeof token === "string" ? token.split(".") : [];
  if (parts.length !== 3 || parts[0] !== "pv1") return null;
  const [, body, mac] = parts;
  const want = sign(secret, body);
  const got = Buffer.from(mac, "base64url");
  if (got.length !== want.length || !timingSafeEqual(got, want)) return null;
  try {
    const { e, p, x } = JSON.parse(Buffer.from(body, "base64url").toString());
    if (typeof e !== "string" || typeof p !== "number" || typeof x !== "number" || x * 1000 <= now) return null;
    return { email: e, preview: p };
  } catch {
    return null;
  }
}
