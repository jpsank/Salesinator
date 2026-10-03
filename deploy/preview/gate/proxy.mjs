// Minimal HTTP + WebSocket-upgrade forwarding with the caller supplying the exact headers to send.
import http from "node:http";
import net from "node:net";

const HOP_BY_HOP = new Set(["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"]);

/** The request's headers minus hop-by-hop ones, then `drop`ped names removed and `set` applied. */
export function headersFor(req, { drop = [], set = {} } = {}) {
  const out = {};
  const dropped = new Set(drop.map((n) => n.toLowerCase()));
  for (const [name, value] of Object.entries(req.headers)) {
    if (!HOP_BY_HOP.has(name) && !dropped.has(name)) out[name] = value;
  }
  for (const [name, value] of Object.entries(set)) {
    if (value === undefined) delete out[name.toLowerCase()];
    else out[name.toLowerCase()] = value;
  }
  return out;
}

export function sendJson(res, status, body, extra = {}) {
  if (res.headersSent) return res.end();
  res.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store", ...extra });
  res.end(JSON.stringify(body));
}

export function forward(req, res, { host, port, headers, onResponseHeaders }) {
  const upstream = http.request({ host, port, method: req.method, path: req.url, headers }, (up) => {
    const out = onResponseHeaders ? onResponseHeaders(up.headers) : up.headers;
    res.writeHead(up.statusCode, out);
    up.pipe(res);
  });
  upstream.on("error", () => sendJson(res, 502, { detail: "The preview is not running." }));
  res.on("close", () => upstream.destroy());
  req.pipe(upstream);
}

/** Hand an upgrade request (WebSocket) to `host:port` and then relay bytes both ways. */
export function tunnel(req, socket, head, { host, port, headers }) {
  const upstream = net.connect(port, host, () => {
    const lines = [`${req.method} ${req.url} HTTP/1.1`];
    for (const [name, value] of Object.entries({ ...headers, connection: "Upgrade", upgrade: req.headers.upgrade })) {
      for (const v of [].concat(value)) lines.push(`${name}: ${v}`);
    }
    upstream.write(`${lines.join("\r\n")}\r\n\r\n`);
    if (head?.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on("error", () => socket.destroy());
  socket.on("error", () => upstream.destroy());
}
