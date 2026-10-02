import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import { WebSocket, WebSocketServer } from "ws";
// @ts-ignore — plain ESM shared with server.mjs, which has no type declarations
import { proxyTo } from "../../../wsProxy.mjs";

/** The `/capture/ingest` relay in server.mjs, against real sockets: Vexa Capture's audio frames go to the capture service and its
 *  replies — including an application close code like 4401 — come back as sent. The wiring below mirrors server.mjs's upgrade handler. */
const open: Array<{ close: () => void }> = [];
afterEach(() => { for (const o of open.splice(0)) try { o.close(); } catch { /* */ } });

const listening = (server: Server) => new Promise<number>((r) => server.listen(0, "127.0.0.1", () => r((server.address() as AddressInfo).port)));

/** A stand-in capture service: says ready, echoes what it receives as text, refuses `api_key=bad` with 4401. */
async function fakeCapture() {
  const received: Array<{ bytes: number; query: string }> = [];
  const server = createServer();
  const wss = new WebSocketServer({ server, path: "/ingest" });
  wss.on("connection", (ws, req) => {
    const query = new URL(req.url || "", "http://x").search;
    if (new URL(req.url || "", "http://x").searchParams.get("api_key") === "bad") { ws.close(4401, "unauthorized"); return; }
    ws.send(JSON.stringify({ type: "ready" }));
    ws.on("message", (d, isBinary) => { received.push({ bytes: (d as Buffer).length, query }); ws.send(`got ${isBinary ? "binary" : "text"} ${(d as Buffer).length}`); });
  });
  const port = await listening(server);
  open.push({ close: () => { wss.close(); server.close(); } });
  return { port, received };
}

/** The relay as server.mjs wires it: an HTTP server whose `upgrade` for /capture/ingest is piped to the capture service, query included. */
async function relay(upstreamPort: number) {
  const server = createServer();
  const captureWss = new WebSocketServer({ noServer: true, maxPayload: 1 << 20 });
  server.on("upgrade", (req, socket, head) => {
    const url = new URL(req.url || "", `http://${req.headers.host}`);
    if (url.pathname !== "/capture/ingest") return;
    captureWss.handleUpgrade(req, socket, head, (client) => { proxyTo(client, socket, `ws://127.0.0.1:${upstreamPort}/ingest${url.search}`); });
  });
  const port = await listening(server);
  open.push({ close: () => { captureWss.close(); server.close(); } });
  return port;
}

const next = (ws: WebSocket) => new Promise<string>((r) => ws.once("message", (d) => r(d.toString())));
const closed = (ws: WebSocket) => new Promise<{ code: number; reason: string }>((r) => ws.once("close", (code, reason) => r({ code, reason: reason.toString() })));

describe("the /capture/ingest relay", () => {
  it("carries audio frames to the capture service and its replies back, with the query (the app's key) intact", async () => {
    const cap = await fakeCapture();
    const port = await relay(cap.port);
    const ws = new WebSocket(`ws://127.0.0.1:${port}/capture/ingest?platform=zoom&native_meeting_id=mac-1&api_key=good`);
    expect(JSON.parse(await next(ws)).type).toBe("ready");
    const reply = next(ws);
    ws.send(Buffer.alloc(6412, 1));
    expect(await reply).toBe("got binary 6412");
    expect(cap.received[0].query).toBe("?platform=zoom&native_meeting_id=mac-1&api_key=good");
    ws.close();
  });

  it("delivers the capture service's refusal — its application close code and reason — to the client", async () => {
    const cap = await fakeCapture();
    const port = await relay(cap.port);
    const ws = new WebSocket(`ws://127.0.0.1:${port}/capture/ingest?platform=zoom&native_meeting_id=mac-1&api_key=bad`);
    expect(await closed(ws)).toEqual({ code: 4401, reason: "unauthorized" });
  });

  it("closes the client when the capture service is unreachable, instead of hanging", async () => {
    const dead = createServer();
    const port0 = await listening(dead); dead.close();
    const port = await relay(port0);
    const ws = new WebSocket(`ws://127.0.0.1:${port}/capture/ingest?api_key=k`);
    ws.on("error", () => { /* an abrupt close surfaces as an error on some Node versions */ });
    const c = await closed(ws);
    expect([1000, 1005, 1006]).toContain(c.code);
  });

  it("drops an oversized frame (the relay's 1 MiB limit) rather than forwarding it", async () => {
    const cap = await fakeCapture();
    const port = await relay(cap.port);
    const ws = new WebSocket(`ws://127.0.0.1:${port}/capture/ingest?api_key=good`);
    await next(ws);
    ws.on("error", () => { /* */ });
    ws.send(Buffer.alloc(2 * 1024 * 1024, 1));
    const c = await closed(ws);
    expect(c.code).toBe(1009);
    expect(cap.received).toEqual([]);
  });
});
