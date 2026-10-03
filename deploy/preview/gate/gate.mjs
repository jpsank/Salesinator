// The preview gate — the only door between a preview and anything real.
//
//   front  (public, behind the tunnel): the viewer's browser → here → the preview's container.
//          Checks who the viewer is (Cloudflare Access), gives the browser an opaque session, and relays.
//   guard  (private network, reached by preview containers): the preview's server → here → the real stack.
//          Swaps the opaque session for the viewer's own short-lived Vexa key and lets only reads through.
//
// A preview container never holds a real credential; this process holds them all.
import http from "node:http";
import { createAccessVerifier } from "./access.mjs";
import { allowGateway, isWhoAmI, READ_ONLY_REFUSAL } from "./policy.mjs";
import { forward, headersFor, sendJson, tunnel } from "./proxy.mjs";
import { issueSession, readSession } from "./session.mjs";
import { createViewerKeys } from "./viewer.mjs";

const AUTH_COOKIE = "vexa-token";
const INFO_COOKIE = "vexa-user-info";
const SESSION_TTL_SECONDS = 6 * 60 * 60;
const CREDENTIAL_HEADERS = ["authorization", "x-api-key", "x-admin-api-key", "x-internal-secret", "x-user-id", "cf-access-jwt-assertion"];

function parseCookies(header) {
  const out = {};
  for (const part of String(header || "").split(";")) {
    const eq = part.indexOf("=");
    if (eq > 0) out[part.slice(0, eq).trim()] = decodeURIComponent(part.slice(eq + 1).trim());
  }
  return out;
}

export function createGate(config, deps = {}) {
  const { domain, sessionSecret, gatewayUpstream, previewHost = (n) => `vexa-preview-pr-${n}`, previewPort = 3000 } = config;
  const verifyAccess =
    deps.verifyAccess ||
    (config.accessTeam
      ? createAccessVerifier({ team: config.accessTeam, audience: config.accessAudience })
      : null);
  const viewerKey = deps.viewerKey || createViewerKeys({ adminUrl: config.adminUrl, adminKey: config.adminKey });
  const hostPattern = new RegExp(`^preview-pr-(\\d+)\\.${domain.replace(/\./g, "\\.")}(:\\d+)?$`);
  const gateway = new URL(gatewayUpstream);

  // ── front ───────────────────────────────────────────────────────────────────────────────────────
  async function identify(req) {
    if (verifyAccess) {
      const cookies = parseCookies(req.headers.cookie);
      return verifyAccess(req.headers["cf-access-jwt-assertion"] || cookies.CF_Authorization);
    }
    return config.devEmail || null; // local use only: no Access team configured
  }

  async function front(req, res, upgrade) {
    const match = hostPattern.exec(String(req.headers.host || "").toLowerCase());
    if (!match) return sendJson(res, 404, { detail: "No such preview." });
    const preview = Number(match[1]);

    const email = await identify(req);
    if (!email) return sendJson(res, 401, { detail: "Sign in required." });

    const viewer = await viewerKey(email);
    if (viewer.status === "no_account") {
      return sendJson(res, 403, { detail: `${email} has no Vexa account yet. Sign in to Vexa once first, then open this link again.` });
    }
    if (viewer.status !== "ok") return sendJson(res, 503, { detail: "Previews are temporarily unavailable." });

    const session = issueSession(sessionSecret, { email, preview, ttlSeconds: SESSION_TTL_SECONDS });
    const secure = req.headers["x-forwarded-proto"] === "https";
    const attrs = `Path=/; HttpOnly; SameSite=Lax; Max-Age=${SESSION_TTL_SECONDS}${secure ? "; Secure" : ""}`;
    const info = JSON.stringify({ email, name: email.split("@")[0] });
    const cookies = { ...parseCookies(req.headers.cookie), [AUTH_COOKIE]: session, [INFO_COOKIE]: info };
    delete cookies.CF_Authorization;
    const headers = headersFor(req, {
      drop: CREDENTIAL_HEADERS,
      set: { cookie: Object.entries(cookies).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("; ") },
    });
    const target = { host: previewHost(preview), port: previewPort, headers };
    if (upgrade) return tunnel(req, upgrade.socket, upgrade.head, target);
    return forward(req, res, {
      ...target,
      onResponseHeaders: (h) => ({
        ...h,
        "x-robots-tag": "noindex",
        "set-cookie": [`${AUTH_COOKIE}=${encodeURIComponent(session)}; ${attrs}`, `${INFO_COOKIE}=${encodeURIComponent(info)}; ${attrs}`, ...[].concat(h["set-cookie"] || [])],
      }),
    });
  }

  // ── guard ───────────────────────────────────────────────────────────────────────────────────────
  function sessionOf(req) {
    return readSession(sessionSecret, req.headers["x-api-key"]);
  }

  async function guardGateway(req, res, upgrade) {
    const session = sessionOf(req);
    if (!session) return sendJson(res, 401, { detail: "Not authenticated" });
    if (!allowGateway(req.method, req.url)) {
      console.log(`[preview-gate] pr-${session.preview} refused ${req.method} ${req.url.split("?")[0]}`);
      return sendJson(res, 403, READ_ONLY_REFUSAL);
    }
    const viewer = await viewerKey(session.email);
    if (viewer.status !== "ok") return sendJson(res, 401, { detail: "Not authenticated" });
    const headers = headersFor(req, { drop: CREDENTIAL_HEADERS, set: { "x-api-key": viewer.key, host: gateway.host } });
    const target = { host: gateway.hostname, port: Number(gateway.port || 80), headers };
    if (upgrade) return tunnel(req, upgrade.socket, upgrade.head, target);
    return forward(req, res, target);
  }

  async function guardAdmin(req, res) {
    if (!isWhoAmI(req.method, req.url)) return sendJson(res, 403, READ_ONLY_REFUSAL);
    const chunks = [];
    for await (const c of req) chunks.push(c);
    let token;
    try {
      token = JSON.parse(Buffer.concat(chunks).toString()).token;
    } catch {
      return sendJson(res, 400, { detail: "Bad request" });
    }
    const session = readSession(sessionSecret, token);
    if (!session) return sendJson(res, 401, { detail: "Not authenticated" });
    const viewer = await viewerKey(session.email);
    if (viewer.status !== "ok") return sendJson(res, 401, { detail: "Not authenticated" });
    return sendJson(res, 200, { user_id: viewer.userId, email: session.email, is_admin: false });
  }

  const wrap = (handler) => (req, res) => handler(req, res).catch(() => sendJson(res, 500, { detail: "Gate error" }));
  // An upgrade that is refused is answered on the socket, not through a ServerResponse.
  const upgradeHandler = (handler) => (req, socket, head) => {
    const res = new http.ServerResponse(req);
    res.assignSocket(socket);
    res.on("finish", () => socket.destroy());
    handler(req, res, { socket, head }).catch(() => socket.destroy());
  };

  return {
    front: { request: wrap((req, res) => front(req, res)), upgrade: upgradeHandler((req, res, up) => front(req, res, up)) },
    guardGateway: { request: wrap((req, res) => guardGateway(req, res)), upgrade: upgradeHandler((req, res, up) => guardGateway(req, res, up)) },
    guardAdmin: { request: wrap((req, res) => guardAdmin(req, res)) },
  };
}

function listen(port, { request, upgrade }) {
  const server = http.createServer(request);
  if (upgrade) server.on("upgrade", upgrade);
  server.listen(port, "0.0.0.0");
  return server;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const env = process.env;
  const need = (name) => {
    if (!env[name]) throw new Error(`${name} is required`);
    return env[name];
  };
  const gate = createGate({
    domain: need("PREVIEW_DOMAIN"),
    sessionSecret: need("PREVIEW_SESSION_SECRET"),
    gatewayUpstream: env.GATEWAY_UPSTREAM || "http://gateway:8000",
    adminUrl: need("VEXA_ADMIN_API_URL").replace(/\/$/, ""),
    adminKey: need("VEXA_ADMIN_API_KEY"),
    accessTeam: env.PREVIEW_ACCESS_TEAM || "",
    accessAudience: env.PREVIEW_ACCESS_AUD || "",
    devEmail: env.PREVIEW_DEV_EMAIL || "",
  });
  if (!env.PREVIEW_ACCESS_TEAM && !env.PREVIEW_DEV_EMAIL) throw new Error("set PREVIEW_ACCESS_TEAM + PREVIEW_ACCESS_AUD (public) or PREVIEW_DEV_EMAIL (local only)");
  if (env.PREVIEW_ACCESS_TEAM && !env.PREVIEW_ACCESS_AUD) throw new Error("PREVIEW_ACCESS_AUD is required with PREVIEW_ACCESS_TEAM");
  listen(8080, gate.front);
  listen(8081, gate.guardGateway);
  listen(8082, gate.guardAdmin);
  console.log(`[preview-gate] up — previews at preview-pr-N.${env.PREVIEW_DOMAIN}${env.PREVIEW_ACCESS_TEAM ? "" : " (LOCAL: no Access check)"}`);
}
