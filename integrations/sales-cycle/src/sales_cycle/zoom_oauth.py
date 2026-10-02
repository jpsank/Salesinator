"""Zoom's OAuth2 flow and the one Zoom API call the auto-join needs.

Each rep authorizes their OWN Zoom account (a user-managed OAuth app), so unlike HubSpot/Slack the tokens
here belong to one rep, not the deployment. Zoom's access tokens last an hour and its refresh tokens
ROTATE: every refresh returns a new refresh token and invalidates the old one, so the new one is persisted
before the access token is used.

Zoom's OAuth reference: https://developers.zoom.us/docs/integrations/oauth/
"""

from __future__ import annotations

import base64
import time
from urllib.parse import urlencode

from sales_cycle._http import call
from sales_cycle.store import Store, ZoomConnection

# Refresh a little early so a slow request never carries a token that expires mid-flight.
EXPIRY_SAFETY_MARGIN_SEC = 60


class ZoomError(RuntimeError):
    pass


def build_authorize_url(*, base_url: str, client_id: str, redirect_uri: str, state: str) -> str:
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state}
    return f"{base_url.rstrip('/')}/oauth/authorize?{urlencode(params)}"


def _basic_auth(client_id: str, client_secret: str) -> dict[str, str]:
    raw = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {"Authorization": f"Basic {raw}", "Content-Type": "application/x-www-form-urlencoded"}


def _token_request(*, base_url: str, client_id: str, client_secret: str, data: dict, what: str) -> dict:
    resp = call(
        "POST", f"{base_url.rstrip('/')}/oauth/token", data=data, headers=_basic_auth(client_id, client_secret),
        timeout=10.0, error_cls=ZoomError, error_prefix=what,
    )
    body = resp.json()
    if not body.get("access_token") or not body.get("refresh_token"):
        raise ZoomError(f"{what} returned no tokens")
    return {
        "access_token": body["access_token"], "refresh_token": body["refresh_token"],
        "expires_at": time.time() + float(body.get("expires_in") or 3600),
    }


def exchange_code(*, base_url: str, client_id: str, client_secret: str, redirect_uri: str, code: str) -> dict:
    """The callback step: Zoom redirected the browser back with ``code`` — trade it for tokens."""
    return _token_request(
        base_url=base_url, client_id=client_id, client_secret=client_secret, what="Zoom token exchange",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
    )


def fetch_me(*, api_base_url: str, access_token: str) -> dict:
    """The authorizing Zoom user: ``id`` is what a meeting's ``host_id`` is later matched against."""
    resp = call(
        "GET", f"{api_base_url.rstrip('/')}/users/me", headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0, error_cls=ZoomError, error_prefix="Zoom user lookup",
    )
    body = resp.json()
    if not body.get("id"):
        raise ZoomError("Zoom user lookup returned no user id")
    return {"id": str(body["id"]), "email": body.get("email") or body.get("display_name")}


def get_valid_access_token(
    *, store: Store, connection: ZoomConnection, base_url: str, client_id: str, client_secret: str,
) -> str:
    """A token good for the next request, refreshed (and the rotated refresh token saved) when it is stale."""
    if connection.expires_at - EXPIRY_SAFETY_MARGIN_SEC > time.time():
        return connection.access_token
    fresh = _token_request(
        base_url=base_url, client_id=client_id, client_secret=client_secret, what="Zoom token refresh",
        data={"grant_type": "refresh_token", "refresh_token": connection.refresh_token},
    )
    store.update_zoom_tokens(connection.vexa_user_id, **fresh)
    return fresh["access_token"]


def revoke(*, base_url: str, client_id: str, client_secret: str, token: str) -> None:
    """Best-effort: tell Zoom the app no longer holds this token. Failure never blocks a disconnect."""
    try:
        call(
            "POST", f"{base_url.rstrip('/')}/oauth/revoke", data={"token": token},
            headers=_basic_auth(client_id, client_secret), timeout=5.0, error_cls=ZoomError, error_prefix="Zoom token revoke",
        )
    except ZoomError:
        pass


def get_meeting(*, api_base_url: str, access_token: str, meeting_id: str) -> dict:
    """The meeting's own record — its ``join_url`` carries the passcode the bot needs."""
    resp = call(
        "GET", f"{api_base_url.rstrip('/')}/meetings/{meeting_id}", headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0, error_cls=ZoomError, error_prefix=f"Zoom meeting {meeting_id} lookup",
    )
    return resp.json()
