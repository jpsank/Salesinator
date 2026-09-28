"""The one "make a web request, and turn a failure into OUR typed error" helper — every client in
this add-on (HubSpot, Slack, Vexa's gateway, Vexa's agent service) was hand-rolling the same six
lines around its own `httpx` call. This is that shared shape; each caller still raises its own error
type with its own message, so a `try/except HubSpotError` at a call site still means exactly what it
said before.
"""

from __future__ import annotations

import httpx


def call(
    method: str, url: str, *, error_cls: type[Exception], error_prefix: str, timeout: float,
    **kwargs,
) -> httpx.Response:
    try:
        resp = httpx.request(method, url, timeout=timeout, **kwargs)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise error_cls(f"{error_prefix} failed: {type(e).__name__}: {e}") from e
    return resp
