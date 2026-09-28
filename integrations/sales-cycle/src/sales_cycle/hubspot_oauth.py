"""HubSpot's OAuth2 flow — a real "Connect HubSpot" button, not a copy-pasted token.

Different from `hubspot_token` (a private-app token that never expires): an OAuth access token
expires every 30 minutes and must be refreshed using the refresh token HubSpot hands back
alongside it. This module is the whole lifecycle: build the consent-screen URL, trade the code
HubSpot sends back for tokens, refresh an expired one, and always hand the caller a token that's
actually still valid right now.

HubSpot's OAuth reference: https://developers.hubspot.com/docs/api/oauth-quickstart-guide
"""

from __future__ import annotations

import time
from urllib.parse import urlencode

from sales_cycle._http import call
from sales_cycle.store import OAuthConnection, Store

PROVIDER = "hubspot"
AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
TOKEN_INFO_URL = "https://api.hubapi.com/oauth/v1/access-tokens"

# Refresh a bit before the token actually expires, so a slow request never gets a token that
# expires mid-flight.
EXPIRY_SAFETY_MARGIN_SEC = 60


class HubSpotOAuthError(RuntimeError):
    pass


def build_authorize_url(*, client_id: str, redirect_uri: str, scopes: str) -> str:
    params = {
        "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": scopes.replace(",", " "),
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _fetch_account_label(access_token: str) -> str | None:
    """The connected HubSpot account's own domain, for a human-readable "Connected as ___"."""
    try:
        resp = call(
            "GET", f"{TOKEN_INFO_URL}/{access_token}",
            error_cls=HubSpotOAuthError, error_prefix="token-info lookup", timeout=5.0,
        )
    except HubSpotOAuthError:
        return None
    return resp.json().get("hub_domain")


def exchange_code(
    *, store: Store, client_id: str, client_secret: str, redirect_uri: str, code: str,
) -> OAuthConnection:
    """The callback step: HubSpot just redirected the browser back to us with `code` — trade it
    for real tokens and save the connection."""
    resp = call(
        "POST", TOKEN_URL,
        data={
            "grant_type": "authorization_code", "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri, "code": code,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10.0,
        error_cls=HubSpotOAuthError, error_prefix="HubSpot token exchange",
    )
    body = resp.json()
    access_token = body["access_token"]
    expires_at = time.time() + float(body["expires_in"])
    account_label = _fetch_account_label(access_token)
    store.save_oauth_connection(
        provider=PROVIDER, access_token=access_token, refresh_token=body.get("refresh_token"),
        expires_at=expires_at, account_label=account_label,
    )
    connection = store.get_oauth_connection(PROVIDER)
    assert connection is not None  # we just saved it
    return connection


def _refresh(
    *, store: Store, client_id: str, client_secret: str, connection: OAuthConnection,
) -> OAuthConnection:
    if not connection.refresh_token:
        raise HubSpotOAuthError("HubSpot connection has no refresh token — reconnect required")
    resp = call(
        "POST", TOKEN_URL,
        data={
            "grant_type": "refresh_token", "client_id": client_id, "client_secret": client_secret,
            "refresh_token": connection.refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10.0,
        error_cls=HubSpotOAuthError, error_prefix="HubSpot token refresh",
    )
    body = resp.json()
    access_token = body["access_token"]
    expires_at = time.time() + float(body["expires_in"])
    store.save_oauth_connection(
        provider=PROVIDER, access_token=access_token,
        refresh_token=body.get("refresh_token", connection.refresh_token),
        expires_at=expires_at, account_label=connection.account_label,
    )
    refreshed = store.get_oauth_connection(PROVIDER)
    assert refreshed is not None
    return refreshed


def get_valid_access_token(
    *, store: Store, client_id: str, client_secret: str,
) -> str | None:
    """The token to actually use right now — refreshed first if it's expired or close to it.
    None if HubSpot was never connected via OAuth (caller falls back to a static token, if any)."""
    connection = store.get_oauth_connection(PROVIDER)
    if connection is None:
        return None
    if connection.expires_at is not None and connection.expires_at - EXPIRY_SAFETY_MARGIN_SEC < time.time():
        connection = _refresh(
            store=store, client_id=client_id, client_secret=client_secret, connection=connection,
        )
    return connection.access_token
