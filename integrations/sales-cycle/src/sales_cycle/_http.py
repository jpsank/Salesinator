"""The one "make a web request, and turn a failure into OUR typed error" helper — every client in
this add-on (HubSpot, Slack, Vexa's gateway, Vexa's agent service) was hand-rolling the same six
lines around its own `httpx` call. This is that shared shape; each caller still raises its own error
type with its own message, so a `try/except HubSpotError` at a call site still means exactly what it
said before.
"""

from __future__ import annotations

import re

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


_SECRETISH = re.compile(r"(gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|xox[abpse]-[A-Za-z0-9-]+|//[^/\s:@]+:[^@\s]+@)")


def error_detail(resp: httpx.Response, *, limit: int = 300) -> str:
    """The reason a service gave for refusing a request (its JSON ``detail``, else its text), with anything token-shaped removed."""
    try:
        body = resp.json()
        text = body.get("detail") if isinstance(body, dict) else None
        text = text if isinstance(text, str) else resp.text
    except ValueError:
        text = resp.text
    return _SECRETISH.sub("<redacted>", " ".join(str(text).split()))[:limit]


def call_detailed(
    method: str, url: str, *, error_cls: type[Exception], error_prefix: str, timeout: float, **kwargs,
) -> httpx.Response:
    """Like ``call``, but a refusal keeps the server's own reason ("HTTP 502: git push failed: … Authentication failed") instead of only the
    status — what lets a caller tell a person WHY (an expired token) rather than that something returned 502."""
    try:
        resp = httpx.request(method, url, timeout=timeout, **kwargs)
    except httpx.HTTPError as e:
        raise error_cls(f"{error_prefix} failed: {type(e).__name__}: {e}") from e
    if resp.status_code >= 400:
        raise error_cls(f"{error_prefix} failed: HTTP {resp.status_code}: {error_detail(resp)}")
    return resp
