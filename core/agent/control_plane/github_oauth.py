"""GitHub's OAuth flow for the per-user reusable git token (git_credentials.py) — a real "Connect
GitHub" button instead of pasting a personal access token. The resulting token lands in the exact
same per-user store a manually pasted PAT would — this only changes how the token is obtained, not
where it's kept or how it's used (workspace_git_sync, push/pull/publish, all unchanged).

Unlike the sales-cycle add-on's HubSpot/Slack connections (one shared connection for a whole
deployment), a GitHub token here is per-VEXA-USER — whoever is logged in when they click "Connect"
is who the resulting token gets saved for. GitHub's callback carries no Vexa session/cookie at all
(it's a plain redirect from github.com, not a same-origin request), so the subject is threaded
through via a signed `state` parameter instead — the standard OAuth pattern for exactly this, and it
doubles as CSRF protection (a forged/replayed state is rejected).

GitHub's OAuth reference: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
MAX_STATE_AGE_SEC = 600  # 10 minutes — plenty to click through GitHub's consent screen


class GitHubOAuthError(RuntimeError):
    pass


def sign_state(*, subject: str, secret: str) -> str:
    """Encodes + signs `subject` so the callback (no session available) can recover who asked,
    without trusting anything the client could forge."""
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{subject}:{ts}".encode(), hashlib.sha256).hexdigest()
    return f"{subject}:{ts}:{sig}"


def verify_state(*, state: str, secret: str, now: float | None = None) -> str:
    """Returns the subject `state` was signed for. Raises on anything forged, malformed, or stale."""
    try:
        subject, ts, sig = state.rsplit(":", 2)
    except ValueError as e:
        raise GitHubOAuthError("malformed state") from e
    expected = hmac.new(secret.encode(), f"{subject}:{ts}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise GitHubOAuthError("state signature mismatch")
    if (now if now is not None else time.time()) - float(ts) > MAX_STATE_AGE_SEC:
        raise GitHubOAuthError("state expired — try connecting again")
    return subject


def build_authorize_url(*, client_id: str, redirect_uri: str, state: str, scopes: str = "repo") -> str:
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scopes, "state": state}
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(
    *, client_id: str, client_secret: str, redirect_uri: str, code: str, timeout: float = 10.0,
) -> str:
    """Trades `code` for an access token. Classic GitHub OAuth Apps issue non-expiring tokens (no
    refresh step needed) — a GitHub App's user-to-server token would need refresh, but that's a
    different registration type than what this targets."""
    body = urllib.parse.urlencode({
        "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "code": code,
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise GitHubOAuthError(f"GitHub token exchange failed: {e}") from e
    if "error" in data:
        raise GitHubOAuthError(f"GitHub rejected the code exchange: {data.get('error_description') or data['error']}")
    token = data.get("access_token")
    if not token:
        raise GitHubOAuthError("GitHub token exchange response missing access_token")
    return str(token)
