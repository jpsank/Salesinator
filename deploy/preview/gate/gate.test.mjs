import assert from "node:assert/strict";
import { generateKeyPairSync, createSign } from "node:crypto";
import http from "node:http";
import { after, before, describe, it } from "node:test";
import { createAccessVerifier } from "./access.mjs";
import { createGate } from "./gate.mjs";
import { allowGateway, isWhoAmI } from "./policy.mjs";
import { issueSession, readSession } from "./session.mjs";
import { createViewerKeys } from "./viewer.mjs";

const SECRET = "test-secret";

describe("session", () => {
  it("round-trips a viewer and preview", () => {
    const s = issueSession(SECRET, { email: "a@b.co", preview: 7, ttlSeconds: 60 });
    assert.deepEqual(readSession(SECRET, s), { email: "a@b.co", preview: 7 });
  });
  it("refuses a forged, re-signed or expired session", () => {
    const s = issueSession(SECRET, { email: "a@b.co", preview: 7, ttlSeconds: 60 });
    assert.equal(readSession("other-secret", s), null);
    const [p, body, mac] = s.split(".");
    const swapped = Buffer.from(JSON.stringify({ e: "boss@b.co", p: 7, x: 9999999999 })).toString("base64url");
    assert.equal(readSession(SECRET, `${p}.${swapped}.${mac}`), null);
    assert.equal(readSession(SECRET, s, Date.now() + 120_000), null);
    assert.equal(readSession(SECRET, "garbage"), null);
    assert.equal(readSession(SECRET, undefined), null);
  });
});

describe("policy", () => {
  it("lets the gateway be read and nothing else", () => {
    assert.equal(allowGateway("GET", "/meetings?x=1"), true);
    assert.equal(allowGateway("HEAD", "/ws"), true);
    for (const m of ["POST", "PUT", "PATCH", "DELETE"]) assert.equal(allowGateway(m, "/bots"), false);
    assert.equal(allowGateway("GET", "/a/../admin"), false);
  });
  it("answers only the who-am-I question for the admin API", () => {
    assert.equal(isWhoAmI("POST", "/internal/validate"), true);
    assert.equal(isWhoAmI("GET", "/internal/validate"), false);
    assert.equal(isWhoAmI("POST", "/admin/users"), false);
  });
});

describe("Cloudflare Access verification", () => {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const jwk = { ...publicKey.export({ format: "jwk" }), kid: "k1" };
  const iss = "https://team.cloudflareaccess.com";
  const token = (claims, key = privateKey, kid = "k1") => {
    const enc = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
    const data = `${enc({ alg: "RS256", kid })}.${enc(claims)}`;
    return `${data}.${createSign("RSA-SHA256").update(data).sign(key).toString("base64url")}`;
  };
  const good = { iss, aud: ["aud-1"], exp: Math.floor(Date.now() / 1000) + 600, email: "Rep@Co.com" };
  const verify = createAccessVerifier({ team: "team", audience: "aud-1", fetchJson: async () => ({ keys: [jwk] }) });

  it("returns the lower-cased email of a valid token", async () => assert.equal(await verify(token(good)), "rep@co.com"));
  it("rejects wrong audience, issuer, expiry and signature", async () => {
    assert.equal(await verify(token({ ...good, aud: ["other"] })), null);
    assert.equal(await verify(token({ ...good, iss: "https://evil.example" })), null);
    assert.equal(await verify(token({ ...good, exp: 1 })), null);
    const other = generateKeyPairSync("rsa", { modulusLength: 2048 }).privateKey;
    assert.equal(await verify(token(good, other)), null);
    assert.equal(await verify(""), null);
    assert.equal(await verify("a.b.c"), null);
  });
});

describe("viewer keys", () => {
  const reply = (status, body) => ({ ok: status < 300, status, json: async () => body });
  it("mints a short-lived key for an existing user, once, and never creates accounts", async () => {
    const calls = [];
    const viewerKey = createViewerKeys({
      adminUrl: "http://admin",
      adminKey: "k",
      fetchImpl: async (url, init) => {
        calls.push([init.method || "GET", url]);
        return url.includes("/admin/users/email/") ? reply(200, { id: 5 }) : reply(200, { token: "real-key" });
      },
    });
    const a = await viewerKey("a@b.co");
    const b = await viewerKey("a@b.co");
    assert.equal(a.key, "real-key");
    assert.equal(b.key, "real-key");
    assert.equal(calls.length, 2, "second call is served from cache");
    assert.ok(calls[1][1].includes("expires_in="));
    assert.ok(!calls.some(([m, u]) => m === "POST" && u.endsWith("/admin/users")));
  });
  it("reports an unknown email as no_account and an outage as unavailable", async () => {
    assert.deepEqual(await createViewerKeys({ adminUrl: "http://a", adminKey: "k", fetchImpl: async () => reply(404, {}) })("x@y.z"), { status: "no_account" });
    assert.deepEqual(await createViewerKeys({ adminUrl: "http://a", adminKey: "k", fetchImpl: async () => { throw new Error("down"); } })("x@y.z"), { status: "unavailable" });
  });
});

describe("gate", () => {
  const seen = { gateway: [], preview: [] };
  let gatewayServer, previewServer, frontServer, guardServer, adminServer, ports;

  const listen = (server) => new Promise((r) => server.listen(0, "127.0.0.1", () => r(server.address().port)));
  const call = (port, { method = "GET", path = "/", headers = {}, body } = {}) =>
    new Promise((resolve, reject) => {
      const req = http.request({ host: "127.0.0.1", port, method, path, headers }, (res) => {
        let text = "";
        res.on("data", (c) => (text += c));
        res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, text }));
      });
      req.on("error", reject);
      req.end(body);
    });

  before(async () => {
    gatewayServer = http.createServer((req, res) => {
      seen.gateway.push({ method: req.method, url: req.url, headers: req.headers });
      res.end("gateway-ok");
    });
    previewServer = http.createServer((req, res) => {
      seen.preview.push({ headers: req.headers });
      res.end("preview-ok");
    });
    const gatewayPort = await listen(gatewayServer);
    const previewPort = await listen(previewServer);
    const gate = createGate(
      {
        domain: "example.test",
        sessionSecret: SECRET,
        gatewayUpstream: `http://127.0.0.1:${gatewayPort}`,
        previewHost: () => "127.0.0.1",
        previewPort,
      },
      {
        verifyAccess: async (t) => (t === "good-jwt" ? "rep@co.com" : t === "new-jwt" ? "new@co.com" : null),
        viewerKey: async (email) => (email === "rep@co.com" ? { status: "ok", key: "REAL-KEY", userId: 5 } : { status: "no_account" }),
      },
    );
    const wrap = (h) => http.createServer(h.request);
    frontServer = wrap(gate.front);
    guardServer = wrap(gate.guardGateway);
    adminServer = wrap(gate.guardAdmin);
    ports = { front: await listen(frontServer), guard: await listen(guardServer), admin: await listen(adminServer) };
  });
  after(() => [gatewayServer, previewServer, frontServer, guardServer, adminServer].forEach((s) => s.close()));

  const host = { host: "preview-pr-7.example.test" };

  it("turns the viewer away without a valid Access identity", async () => {
    const r = await call(ports.front, { headers: host });
    assert.equal(r.status, 401);
    assert.equal(seen.preview.length, 0);
  });
  it("404s hosts that are not previews", async () => {
    assert.equal((await call(ports.front, { headers: { host: "other.example.test", "cf-access-jwt-assertion": "good-jwt" } })).status, 404);
  });
  it("hands a signed-in viewer's request to the preview with an opaque session, never a real credential", async () => {
    const r = await call(ports.front, {
      headers: { ...host, "cf-access-jwt-assertion": "good-jwt", cookie: "vexa-token=evil; CF_Authorization=x", "x-api-key": "stolen", authorization: "Bearer stolen" },
    });
    assert.equal(r.status, 200);
    const sent = seen.preview.at(-1).headers;
    const token = /vexa-token=([^;]+)/.exec(sent.cookie)[1];
    assert.deepEqual(readSession(SECRET, decodeURIComponent(token)), { email: "rep@co.com", preview: 7 });
    assert.ok(!JSON.stringify(sent).includes("REAL-KEY"));
    assert.ok(!sent.cookie.includes("CF_Authorization"));
    assert.deepEqual(JSON.parse(decodeURIComponent(/vexa-user-info=([^;]+)/.exec(sent.cookie)[1])), { email: "rep@co.com", name: "rep" });
    assert.equal(sent["x-api-key"], undefined);
    assert.equal(sent.authorization, undefined);
    assert.equal(sent["cf-access-jwt-assertion"], undefined);
    assert.ok(r.headers["set-cookie"].some((c) => c.startsWith("vexa-token=") && c.includes("HttpOnly")));
  });
  it("tells a viewer with no Vexa account what to do", async () => {
    const r = await call(ports.front, { headers: { ...host, "cf-access-jwt-assertion": "new-jwt" } });
    assert.equal(r.status, 403);
    assert.match(JSON.parse(r.text).detail, /new@co\.com has no Vexa account/);
  });

  const session = () => issueSession(SECRET, { email: "rep@co.com", preview: 7, ttlSeconds: 60 });

  it("guard: swaps the session for the viewer's own key on a read", async () => {
    const r = await call(ports.guard, { path: "/meetings", headers: { "x-api-key": session(), "x-admin-api-key": "nope" } });
    assert.equal(r.text, "gateway-ok");
    const sent = seen.gateway.at(-1);
    assert.equal(sent.headers["x-api-key"], "REAL-KEY");
    assert.equal(sent.headers["x-admin-api-key"], undefined);
  });
  it("guard: refuses every write, and any request without a valid session", async () => {
    const before = seen.gateway.length;
    for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
      assert.equal((await call(ports.guard, { method, path: "/bots", headers: { "x-api-key": session() } })).status, 403);
    }
    assert.equal((await call(ports.guard, { path: "/meetings" })).status, 401);
    assert.equal((await call(ports.guard, { path: "/meetings", headers: { "x-api-key": "REAL-KEY" } })).status, 401);
    assert.equal(seen.gateway.length, before);
  });
  it("admin guard: answers who-am-I for a session and refuses everything else", async () => {
    const ok = await call(ports.admin, { method: "POST", path: "/internal/validate", body: JSON.stringify({ token: session() }) });
    assert.deepEqual(JSON.parse(ok.text), { user_id: 5, email: "rep@co.com", is_admin: false });
    assert.equal((await call(ports.admin, { method: "POST", path: "/internal/validate", body: JSON.stringify({ token: "x" }) })).status, 401);
    assert.equal((await call(ports.admin, { method: "POST", path: "/admin/users", body: "{}" })).status, 403);
    assert.equal((await call(ports.admin, { path: "/admin/users" })).status, 403);
  });
});
