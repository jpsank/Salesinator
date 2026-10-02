// Custom Next.js server with a real server-side WebSocket proxy for `/ws`.
//
// Why this exists: Next.js `rewrites()` proxy HTTP only — they do NOT proxy the
// WebSocket `upgrade` handshake. So a browser opening same-origin `wss://host/ws`
// never reaches the gateway. This server intercepts the HTTP `upgrade` event for
// path `/ws`, opens a *server-side* socket to the gateway with the `x-api-key`
// header (key stays server-side, never in any client-visible URL), and pipes
// frames bidirectionally. The browser connects KEYLESS to same-origin `/ws`.
//
// The key injected here is the SAME per-user key the REST proxy forwards
// (src/app/api/proxyAuth.ts): the logged-in user's APIToken from the `vexa-token`
// cookie, falling back to VEXA_API_KEY / VEXA_BOT_API_KEY. This MUST match the REST
// side — the gateway auto-subscribes the socket to `u:{user_id}:meetings` from this
// key, so a mismatched key would deliver another user's live meeting.status frames
// (or none), freezing the client's meeting list at its last REST snapshot.
import { createServer } from "node:http";
import nextEnv from "@next/env";
import next from "next";
import { WebSocketServer } from "ws";
import { attachSocketError, endSocket, logError, proxyTo } from "./wsProxy.mjs";

const dev = process.env.NODE_ENV !== "production";
const { loadEnvConfig } = nextEnv;
loadEnvConfig(process.cwd(), dev);

const port = parseInt(process.env.PORT || "3000", 10);
const hostname = process.env.HOST || "0.0.0.0";

const GATEWAY_URL = (process.env.GATEWAY_URL || "ws://127.0.0.1:18056")
  .replace(/\/$/, "")
  .replace(/^http/, "ws");

// The httpOnly cookie carrying the logged-in user's APIToken (set by /api/auth/login).
// Mirrors AUTH_COOKIE in src/app/api/auth/adminApi.ts.
const AUTH_COOKIE = process.env.VEXA_AUTH_COOKIE_NAME || "vexa-token";

/** Resolve the x-api-key to send upstream on the `/ws` upgrade, PER REQUEST. The gateway resolves the
 *  user_id from this key at connect and auto-subscribes the socket to `u:{user_id}:meetings` — so it MUST
 *  be the same per-user key the REST proxy forwards (src/app/api/proxyAuth.ts), else the live meeting.status
 *  frames land on a different user's channel and the client's list never advances past its last snapshot.
 *  Resolution mirrors proxyAuth.ts: cookie token → VEXA_API_KEY → VEXA_BOT_API_KEY → "". */
function resolveUpstreamKey(req) {
  const cookieToken = readCookie(req.headers.cookie, AUTH_COOKIE);
  return cookieToken || process.env.VEXA_API_KEY || process.env.VEXA_BOT_API_KEY || "";
}

/** Pull a single cookie value out of a raw `Cookie` header. Returns undefined if absent. */
function readCookie(header, name) {
  if (!header) return undefined;
  for (const part of header.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) {
      return decodeURIComponent(part.slice(eq + 1).trim());
    }
  }
  return undefined;
}

process.on("unhandledRejection", (reason) => {
  logError("unhandled promise rejection", reason);
});

process.on("uncaughtException", (err) => {
  logError("uncaught exception", err);
});

const app = next({ dev, hostname, port });
const handle = app.getRequestHandler();

await app.prepare();

const server = createServer((req, res) => {
  Promise.resolve(handle(req, res)).catch((err) => {
    logError("request handler failed", err);
    sendProxyError(res);
  });
});

// Browser-facing WS server — we do the upgrade ourselves (noServer) only for `/ws` and `/capture/ingest`.
const wss = new WebSocketServer({ noServer: true });

// Vexa Capture (the Mac app) streams a call's audio to `/capture/ingest`, so a deployment needs ONE public address — this
// site — rather than a second one for the capture service. The upgrade is relayed as-is, query included: the app's own
// API key rides it (`?api_key=`) and is checked by the capture service, never by this server. No cookie, no env key.
const CAPTURE_UPSTREAM = (process.env.CAPTURE_INGEST_UPSTREAM || "ws://127.0.0.1:19099/ingest").replace(/\/$/, "");
const captureWss = new WebSocketServer({ noServer: true, maxPayload: 1 << 20 });      // an audio frame is ~6 KB

server.on("upgrade", (req, socket, head) => {
  let pathname;
  try {
    pathname = new URL(req.url, `http://${req.headers.host}`).pathname;
  } catch {
    socket.destroy();
    return;
  }
  if (pathname === "/capture/ingest") {
    const search = new URL(req.url, `http://${req.headers.host}`).search;
    let closeOnSocketError = () => endSocket(socket);
    attachSocketError(socket, "capture client upgrade", () => closeOnSocketError());
    captureWss.handleUpgrade(req, socket, head, (client) => {
      closeOnSocketError = proxyTo(client, socket, `${CAPTURE_UPSTREAM}${search}`);
    });
    return;
  }
  if (pathname !== "/ws") {
    // Let Next/HMR handle its own upgrades (e.g. `_next/webpack-hmr` in dev).
    return;
  }
  // Resolve the per-user key from THIS request's cookie before the upgrade completes (req.headers are
  // gone once we hand off to the WS client). Falls back to the env keys for keyless / single-key deploys.
  const apiKey = resolveUpstreamKey(req);
  let closeOnSocketError = () => endSocket(socket);
  attachSocketError(socket, "client upgrade", () => closeOnSocketError());
  wss.handleUpgrade(req, socket, head, (client) => {
    closeOnSocketError = proxyTo(client, socket, `${GATEWAY_URL}/ws`, apiKey ? { "x-api-key": apiKey } : {});
  });
});

server.on("clientError", (err, socket) => {
  logError("http client socket error", err);
  endSocket(socket);
});

server.on("error", (err) => {
  logError("http server error", err);
});

wss.on("error", (err) => {
  logError("websocket server error", err);
});

function sendProxyError(res) {
  if (res.destroyed || res.writableEnded) return;
  try {
    if (res.headersSent) {
      res.end();
      return;
    }
    res.writeHead(502, {
      "Content-Type": "application/json",
      "Cache-Control": "no-cache",
    });
    res.end(JSON.stringify({ error: "upstream_unavailable" }));
  } catch (err) {
    logError("failed to send proxy error response", err);
  }
}

server.listen(port, hostname, () => {
  // eslint-disable-next-line no-console
  console.log(`> Terminal ready on http://${hostname}:${port} (WS proxy /ws -> ${GATEWAY_URL}/ws, /capture/ingest -> ${CAPTURE_UPSTREAM})`);
});
