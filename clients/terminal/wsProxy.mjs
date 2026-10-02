// WebSocket proxying for the custom server (server.mjs): pipe a browser/app socket to an upstream socket, both ways.
// Lives in its own module so it can be tested against real sockets without booting Next.
import { WebSocket } from "ws";

/** Pipe `client` to a new socket opened at `target` (with `headers`). Frames and close codes pass through unchanged —
 *  an application close code such as the capture ingest's 4401 reaches the client as it was sent. Returns a function that
 *  closes both sides. */
export function proxyTo(client, clientSocket, target, headers = {}) {
  const upstream = new WebSocket(target, { headers });

  const pending = [];
  let upstreamOpen = false;

  const closePair = () => {
    pending.length = 0;
    safeClose(client);
    safeClose(upstream);
  };
  const onProxyError = (scope, err) => {
    logError(scope, err);
    closePair();
  };

  attachSocketError(clientSocket || client._socket, "client websocket", (err) => onProxyError("client websocket socket error", err));
  attachSocketError(upstream._socket, "upstream websocket", (err) => onProxyError("upstream websocket socket error", err));

  client.on("message", (data, isBinary) => {
    if (upstreamOpen && upstream.readyState === WebSocket.OPEN) {
      sendFrame(upstream, data, { binary: isBinary }, "client -> upstream", closePair);
    } else if (upstream.readyState === WebSocket.CONNECTING) {
      pending.push([data, isBinary]);
    }
  });

  upstream.on("open", () => {
    upstreamOpen = true;
    attachSocketError(upstream._socket, "upstream websocket", (err) => onProxyError("upstream websocket socket error", err));
    for (const [data, isBinary] of pending) {
      sendFrame(upstream, data, { binary: isBinary }, "client -> upstream", closePair);
    }
    pending.length = 0;
  });
  upstream.on("upgrade", () => {
    attachSocketError(upstream._socket, "upstream websocket", (err) => onProxyError("upstream websocket socket error", err));
  });
  upstream.on("message", (data, isBinary) => {
    sendFrame(client, data, { binary: isBinary }, "upstream -> client", closePair);
  });
  upstream.on("unexpected-response", (_req, res) => {
    logError("upstream websocket rejected upgrade", new Error(`HTTP ${res.statusCode}`));
    closePair();
  });

  // Close each side when the other closes. Only forward a code if it's a valid
  // application close code (1000 / 3000-4999); reserved codes like 1005/1006
  // would throw, so fall back to a bare close.
  client.on("close", (code, reason) => safeClose(upstream, code, reason));
  upstream.on("close", (code, reason) => safeClose(client, code, reason));
  client.on("error", (err) => onProxyError("client websocket error", err));
  upstream.on("error", (err) => onProxyError("upstream websocket error", err));

  return closePair;
}

const socketErrorHandlers = new WeakSet();

export function attachSocketError(socket, scope, onError) {
  if (!socket || socketErrorHandlers.has(socket)) return;
  socketErrorHandlers.add(socket);
  socket.on("error", (err) => {
    logError(scope, err);
    onError?.(err);
  });
}

function sendFrame(sock, data, options, scope, onError) {
  if (sock.readyState !== WebSocket.OPEN) return;
  try {
    sock.send(data, options, (err) => {
      if (!err) return;
      logError(`${scope} send failed`, err);
      onError?.(err);
    });
  } catch (err) {
    logError(`${scope} send failed`, err);
    onError?.(err);
  }
}

export function safeClose(sock, code, reason) {
  if (!sock || sock.readyState === WebSocket.CLOSING || sock.readyState === WebSocket.CLOSED) return;
  try {
    if (code === 1000 || (code >= 3000 && code <= 4999)) sock.close(code, reason);
    else sock.close();
  } catch (err) {
    logError("websocket close failed", err);
  }
}


export function endSocket(socket) {
  if (!socket || socket.destroyed) return;
  try {
    socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
  } catch (err) {
    logError("socket end failed", err);
  }
}

export function logError(scope, err) {
  // eslint-disable-next-line no-console
  console.error(`[terminal-server] ${scope}`, err);
}

