"""The one "Connect X" OAuth flow, registered per provider — HubSpot and Slack are identical
shapes underneath (authorize → redirect to provider → callback trades code for a token → status/
disconnect back the Settings page), so this is written once and both call it, instead of two
near-identical copies of the same four routes (a third provider should too).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Protocol

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from sales_cycle.settings import get_settings
from sales_cycle.store import Store

logger = logging.getLogger("sales_cycle.oauth_routes")


@dataclass(frozen=True)
class OAuthProviderConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: str


class ExchangeCode(Protocol):
    def __call__(
        self, *, store: Store, client_id: str, client_secret: str, redirect_uri: str, code: str,
    ) -> object: ...


class OAuthTokenBody(BaseModel):
    model_config = {"extra": "forbid"}
    token: str


def register_oauth_routes(
    app: FastAPI, *, provider: str,
    build_authorize_url: Callable[..., str],
    exchange_code: ExchangeCode,
    error_cls: type[Exception],
    get_config: Callable[[], OAuthProviderConfig],
    get_store: Callable[[], Store],
) -> None:
    """`get_config` is a callable, not a value, so each request reads live settings (env vars)
    rather than whatever was configured when the app started. `get_store` is threaded in (rather
    than imported directly) to avoid a circular import: `api.py` imports this module."""

    @app.get(f"/oauth/{provider}/authorize", name=f"{provider}_oauth_authorize")
    def _authorize() -> RedirectResponse:
        cfg = get_config()
        if not cfg.client_id or not cfg.redirect_uri:
            raise HTTPException(status_code=503, detail=f"{provider} OAuth is not configured on this deployment")
        return RedirectResponse(
            build_authorize_url(client_id=cfg.client_id, redirect_uri=cfg.redirect_uri, scopes=cfg.scopes)
        )

    @app.get(f"/oauth/{provider}/callback", name=f"{provider}_oauth_callback")
    def _callback(code: str | None = None, error: str | None = None) -> RedirectResponse:
        """The provider redirects the browser HERE directly (not through Vexa) after the user
        approves or declines — this connection is deployment-wide, not tied to whichever Vexa
        account happens to be logged in, so there's no Vexa session to thread through this leg."""
        settings_url = f"{get_settings().terminal_url.rstrip('/')}/?settings=sales-cycle"
        if error or not code:
            return RedirectResponse(f"{settings_url}&{provider}_error={error or 'no_code'}")
        cfg = get_config()
        try:
            exchange_code(
                store=get_store(), client_id=cfg.client_id, client_secret=cfg.client_secret,
                redirect_uri=cfg.redirect_uri, code=code,
            )
        except error_cls as e:
            logger.error("%s OAuth code exchange failed: %s", provider, e)
            return RedirectResponse(f"{settings_url}&{provider}_error=exchange_failed")
        return RedirectResponse(f"{settings_url}&{provider}_connected=1")

    @app.get(f"/oauth/{provider}/status", name=f"{provider}_oauth_status")
    def _status() -> dict:
        cfg = get_config()
        configured = bool(cfg.client_id and cfg.redirect_uri)
        connection = get_store().get_oauth_connection(provider)
        if connection is None:
            return {"connected": False, "configured": configured}
        return {"connected": True, "configured": configured, "account_label": connection.account_label}

    @app.post(f"/oauth/{provider}/disconnect", name=f"{provider}_oauth_disconnect")
    def _disconnect() -> dict:
        get_store().disconnect_oauth(provider)
        return {"connected": False}

    @app.post(f"/oauth/{provider}/token", name=f"{provider}_oauth_set_token")
    def _set_token(body: OAuthTokenBody) -> dict:
        """Paste a token directly instead of going through OAuth — e.g. HubSpot's Service Key /
        private-app token, for whoever's account can't or doesn't want to register an OAuth app.
        Stored in the exact same place an OAuth-obtained token would be, so every other code path
        (get_valid_access_token, status, disconnect) treats it identically — it just has no
        refresh_token or expiry, so it's handed back as-is forever, same as a static env-var token."""
        token = body.token.strip()
        if not token:
            raise HTTPException(status_code=400, detail="token must not be empty")
        get_store().save_oauth_connection(
            provider=provider, access_token=token, refresh_token=None, expires_at=None, account_label=None,
        )
        cfg = get_config()
        return {"connected": True, "configured": bool(cfg.client_id and cfg.redirect_uri)}
