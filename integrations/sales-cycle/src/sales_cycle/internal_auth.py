"""Which of this service's routes the internet may reach, and the shared secret that guards the rest.

The service is published on a public address because Slack, Zoom, HubSpot and the browser's OAuth redirects have to reach it —
and a published address exposes EVERY route, not just those. So the split is explicit:

* **Public, and why each is safe:** the OAuth ``authorize`` redirect (it only sends the browser to the provider) and ``callback``
  (the provider's own redirect), ``/slack/events`` and ``/webhooks/*`` (each verifies the sender's signature), ``/tag`` and
  ``/dispatch`` (they act only under a Vexa API key the caller must hold), and ``/health``.
* **Private — need ``X-Internal-Secret``:** everything else. That is the connection status/disconnect/paste-a-token routes, the Slack
  channel settings, ``/internal/*`` (the sweeps), and ``/zoom/*``. Only Vexa's own Terminal and this stack's sweep loop hold the
  secret.

A route added later is private unless it is added to ``PUBLIC`` — the safe default, so a new endpoint is never published by accident.
"""

from __future__ import annotations

import hmac
import re

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sales_cycle.settings import get_settings

PUBLIC = [
    re.compile(p) for p in (
        r"^/health$",
        r"^/oauth/[a-z0-9_-]+/(authorize|callback)$",
        r"^/slack/events$",
        r"^/webhooks/[a-z0-9_-]+$",
        r"^/tag$",
        r"^/dispatch$",
    )
]


def is_public(path: str) -> bool:
    return any(p.match(path) for p in PUBLIC)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _require_internal_secret(request: Request, call_next):
        if is_public(request.url.path):
            return await call_next(request)
        secret = get_settings().internal_secret
        if not secret:
            return JSONResponse({"detail": "SALES_CYCLE_INTERNAL_SECRET is not configured — private routes are closed"}, status_code=503)
        if not hmac.compare_digest(request.headers.get("X-Internal-Secret", ""), secret):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)
