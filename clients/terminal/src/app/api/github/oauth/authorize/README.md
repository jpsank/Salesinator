# api/github/oauth/authorize

[`route.ts`](route.ts) — redirects the browser to agent-api's `GET /api/workspace/git-token/oauth/authorize`
to start the "Connect GitHub" flow. No `?for=` forwarding — this is the caller's own personal
GitHub connection; the product-repo picker (folded into this same card) reuses whichever token
this flow produces rather than authenticating a second, separate identity.
