"""Slack's OAuth2 flow — a real "Connect Slack" button, not a copy-pasted bot token.

Simpler than hubspot_oauth.py: a standard (non-rotating) Slack bot token doesn't expire, so there's
no refresh step — just the one-time trade of a `code` for a token, done once at connect time.

Slack's OAuth reference: https://api.slack.com/authentication/oauth-v2
"""

from __future__ import annotations

from urllib.parse import urlencode

from sales_cycle._http import call
from sales_cycle.store import OAuthConnection, Store

PROVIDER = "slack"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
TOKEN_URL = "https://slack.com/api/oauth.v2.access"


class SlackOAuthError(RuntimeError):
    pass


def build_authorize_url(*, client_id: str, redirect_uri: str, scopes: str) -> str:
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": scopes}
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(
    *, store: Store, client_id: str, client_secret: str, redirect_uri: str, code: str,
) -> OAuthConnection:
    """The callback step: Slack just redirected the browser back to us with `code` — trade it for
    a real bot token and save the connection. Slack's own error convention is a 200 with
    `{"ok": false, "error": "..."}`, not an HTTP error status, so that's checked explicitly here."""
    resp = call(
        "POST", TOKEN_URL,
        data={
            "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri, "code": code,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10.0,
        error_cls=SlackOAuthError, error_prefix="Slack token exchange",
    )
    body = resp.json()
    if not body.get("ok"):
        raise SlackOAuthError(f"Slack rejected the code exchange: {body.get('error')}")
    access_token = body["access_token"]
    team = body.get("team") or {}
    store.save_oauth_connection(
        provider=PROVIDER, access_token=access_token, refresh_token=None, expires_at=None,
        account_label=team.get("name"),
    )
    connection = store.get_oauth_connection(PROVIDER)
    assert connection is not None  # we just saved it
    return connection


def get_access_token(*, store: Store) -> str | None:
    """The token to use right now, or None if Slack was never connected via OAuth (caller falls
    back to a static bot token, if any). No expiry check — see the module docstring."""
    connection = store.get_oauth_connection(PROVIDER)
    return connection.access_token if connection is not None else None
