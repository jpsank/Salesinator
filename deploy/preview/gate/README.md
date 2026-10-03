# gate — the one door between a preview and anything real

Zero-dependency Node service (`node:http`, `node:net`, `node:crypto`). It runs as the `preview-gate` container and has
two faces (see [`../README.md`](../README.md) for the picture):

- **front** (`:8080`, behind the tunnel) — identifies the viewer from Cloudflare Access, gives the browser an opaque
  session cookie, and relays to that preview's container.
- **guard** (`:8081` gateway, `:8082` admin; private network) — turns a preview's opaque session into the viewer's own
  short-lived Vexa key and lets only reads through.

| File | Job |
|---|---|
| `gate.mjs` | wires the two faces; the only place real credentials are read |
| `access.mjs` | verifies Cloudflare Access's signed JWT (signature, issuer, audience, expiry) |
| `session.mjs` | signs and reads the opaque session a preview holds instead of a real token |
| `viewer.mjs` | email → that person's Vexa key (looked up, never created; 6-hour expiry; cached) |
| `policy.mjs` | what a preview may ask: reads on the gateway, one "who am I" answer from the admin API |
| `proxy.mjs` | HTTP and WebSocket-upgrade forwarding with exact, caller-chosen headers |

Tests: `node --test deploy/preview/gate/`.
