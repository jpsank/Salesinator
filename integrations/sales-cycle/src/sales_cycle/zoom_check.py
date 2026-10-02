"""zoom_check.py — is "Connect Zoom" ready for the first real call?

Run it after registering the Zoom app and setting its four values, from wherever the service's settings are loaded::

    docker exec <sales-cycle container> python -m sales_cycle.zoom_check [--base https://sales-cycle.example.com]

It checks what can be proved without a Zoom account — the four settings are present, the redirect address has the shape
Zoom's consent screen must be given, and the public address really reaches this service: it answers Zoom's
endpoint-validation handshake with the configured secret token, refuses an unsigned notification, and refuses the
per-rep routes without the internal secret. No secret is printed. What it cannot prove — that Zoom accepts the client
id, and that a real ``meeting.started`` carries the fields ``zoom_join`` reads — is left for the first real call.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import secrets
import sys
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from sales_cycle.settings import Settings, get_settings

CALLBACK_PATH = "/oauth/zoom/callback"
TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class Check:
    ok: bool
    name: str
    detail: str


def _settings_present(s: Settings) -> Check:
    required = {
        "SALES_CYCLE_ZOOM_OAUTH_CLIENT_ID": s.zoom_oauth_client_id,
        "SALES_CYCLE_ZOOM_OAUTH_CLIENT_SECRET": s.zoom_oauth_client_secret,
        "SALES_CYCLE_ZOOM_OAUTH_REDIRECT_URI": s.zoom_oauth_redirect_uri,
        "SALES_CYCLE_ZOOM_WEBHOOK_SECRET_TOKEN": s.zoom_webhook_secret_token,
        "SALES_CYCLE_INTERNAL_SECRET": s.internal_secret,
    }
    missing = [k for k, v in required.items() if not v]
    return Check(not missing, "settings", "all set" if not missing else "missing: " + ", ".join(missing))


def _redirect_shape(redirect_uri: str) -> Check:
    u = urlparse(redirect_uri)
    if u.scheme != "https" or not u.netloc:
        return Check(False, "redirect address", "must be a public https:// address — Zoom refuses anything else")
    if u.path != CALLBACK_PATH:
        return Check(False, "redirect address", f"the path must be {CALLBACK_PATH}, not {u.path or '/'}")
    return Check(True, "redirect address", f"{u.scheme}://{u.netloc}{CALLBACK_PATH}")


def _status(r: httpx.Response | Exception) -> str:
    return f"unreachable ({type(r).__name__})" if isinstance(r, Exception) else f"HTTP {r.status_code}"


def _send(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response | Exception:
    try:
        return client.request(method, url, **kw)
    except httpx.HTTPError as e:
        return e


def _handshake(client: httpx.Client, base: str, secret_token: str) -> Check:
    plain = secrets.token_urlsafe(24)
    r = _send(client, "POST", f"{base}/webhooks/zoom", json={"event": "endpoint.url_validation", "payload": {"plainToken": plain}})
    if isinstance(r, Exception) or r.status_code != 200:
        return Check(False, "webhook handshake", f"{base}/webhooks/zoom did not answer the validation request — {_status(r)}")
    expected = hmac.new(secret_token.encode(), plain.encode(), hashlib.sha256).hexdigest()
    try:
        got = str(r.json().get("encryptedToken", ""))
    except ValueError:
        got = ""
    if not hmac.compare_digest(got, expected):
        return Check(False, "webhook handshake", "answered, but with a different secret token than the one configured here")
    return Check(True, "webhook handshake", "answers Zoom's validation with the configured secret token")


def _refuses(client: httpx.Client, base: str, name: str, method: str, path: str, expected: set[int], **kw) -> Check:
    r = _send(client, method, f"{base}{path}", **kw)
    if isinstance(r, Exception):
        return Check(False, name, f"{base}{path} — {_status(r)}")
    return Check(r.status_code in expected, name, f"{path} → {_status(r)}" + ("" if r.status_code in expected else f" (expected {sorted(expected)})"))


def run(s: Settings, *, base: str | None = None, client: httpx.Client | None = None) -> list[Check]:
    checks = [_settings_present(s), _redirect_shape(s.zoom_oauth_redirect_uri)]
    origin = base or (lambda u: f"{u.scheme}://{u.netloc}" if u.netloc else "")(urlparse(s.zoom_oauth_redirect_uri))
    if not origin or not s.zoom_webhook_secret_token:
        checks.append(Check(False, "public address", "no address to probe — set the redirect address and secret token, or pass --base"))
        return checks
    origin = origin.rstrip("/")
    own = client or httpx.Client(timeout=TIMEOUT_SEC, follow_redirects=False)
    try:
        checks.append(_handshake(own, origin, s.zoom_webhook_secret_token))
        checks.append(_refuses(own, origin, "unsigned notification refused", "POST", "/webhooks/zoom", {401},
                               json={"event": "meeting.started", "payload": {"object": {"id": "1", "host_id": "x"}}}))
        checks.append(_refuses(own, origin, "per-rep routes need the internal secret", "POST", "/zoom/disconnect", {401},
                               json={"vexa_user_id": "probe"}))
    finally:
        if client is None:
            own.close()
    return checks


def format_report(checks: list[Check]) -> str:
    lines = [f"  {'✓' if c.ok else '✗'} {c.name} — {c.detail}" for c in checks]
    lines.append("")
    lines.append("All checked parts are ready." if all(c.ok for c in checks) else "Fix the ✗ items above and run this again.")
    lines.append("Not checked here: that Zoom accepts the client id, and the fields of a real meeting.started — confirm on the first real call.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that Connect Zoom is ready for its first real call.")
    parser.add_argument("--base", help="public address of this service (default: the host of the Zoom redirect address)")
    args = parser.parse_args(argv)
    checks = run(get_settings(), base=args.base)
    print(format_report(checks))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
