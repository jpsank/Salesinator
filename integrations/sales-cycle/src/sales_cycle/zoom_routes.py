"""The "Connect Zoom" routes: authorize → Zoom's consent screen → callback, per-rep status and disconnect, and
the webhook Zoom calls when a connected rep starts a meeting.

Written as its own registration (like ``oauth_routes``) rather than inside api.py. Two audiences, two kinds of
protection: the per-rep routes (``/zoom/*``) act for one particular Vexa user, so they require the shared secret
only Vexa's Terminal holds — this service is reachable from the internet; the two routes Zoom itself calls
(``/oauth/zoom/callback``, ``/webhooks/zoom``) are public and protected by the OAuth ``state`` and the
notification signature respectively.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from typing import Callable

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from sales_cycle import zoom_oauth
from sales_cycle.settings import get_settings
from sales_cycle.store import Store
from sales_cycle.zoom_join import join_started_meeting
from sales_cycle.zoom_verify import ZoomSignatureError, url_validation_response, verify_zoom_signature

logger = logging.getLogger("sales_cycle.zoom_routes")

# A rep has this long to finish Zoom's consent screen before the pending authorization is discarded.
PENDING_MAX_AGE_SEC = 900.0


class AuthorizeLinkBody(BaseModel):
    model_config = {"extra": "forbid"}
    vexa_user_id: str
    vexa_token: str
    vexa_token_id: str | int | None = None


class UserBody(BaseModel):
    model_config = {"extra": "forbid"}
    vexa_user_id: str


def _configured() -> bool:
    s = get_settings()
    return bool(s.zoom_oauth_client_id and s.zoom_oauth_client_secret and s.zoom_oauth_redirect_uri)


def _require_internal(request: Request) -> None:
    secret = get_settings().internal_secret
    if not secret:
        raise HTTPException(status_code=503, detail="SALES_CYCLE_INTERNAL_SECRET is not configured")
    if not hmac.compare_digest(request.headers.get("X-Internal-Secret", ""), secret):
        raise HTTPException(status_code=401, detail="unauthorized")


def register_zoom_routes(app: FastAPI, *, get_store: Callable[[], Store]) -> None:

    @app.post("/zoom/authorize-link", name="zoom_authorize_link")
    def _authorize_link(body: AuthorizeLinkBody, request: Request) -> dict:
        """The Terminal has verified who is signed in and minted a bot-scoped key for them; this remembers both
        under a one-time state and returns where to send the browser."""
        _require_internal(request)
        if not _configured():
            raise HTTPException(status_code=503, detail="Zoom OAuth is not configured on this deployment")
        s = get_settings()
        nonce = secrets.token_urlsafe(32)
        get_store().add_zoom_pending(
            nonce=nonce, vexa_user_id=body.vexa_user_id, vexa_token=body.vexa_token,
            vexa_token_id=None if body.vexa_token_id is None else str(body.vexa_token_id),
        )
        return {"url": zoom_oauth.build_authorize_url(
            base_url=s.zoom_oauth_base_url, client_id=s.zoom_oauth_client_id,
            redirect_uri=s.zoom_oauth_redirect_uri, state=nonce,
        )}

    @app.get("/oauth/zoom/callback", name="zoom_oauth_callback")
    def _callback(code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
        """Zoom redirects the browser here directly. ``state`` is the one-time nonce from ``/zoom/authorize-link``;
        without a live one there is no Vexa user to attach the connection to."""
        s = get_settings()
        done_url = f"{s.terminal_url.rstrip('/')}/?settings=integrations"
        pending = get_store().take_zoom_pending(state, max_age_sec=PENDING_MAX_AGE_SEC) if state else None
        if error or not code:
            return RedirectResponse(f"{done_url}&zoom_error={error or 'no_code'}")
        if pending is None:
            return RedirectResponse(f"{done_url}&zoom_error=expired")
        try:
            tokens = zoom_oauth.exchange_code(
                base_url=s.zoom_oauth_base_url, client_id=s.zoom_oauth_client_id,
                client_secret=s.zoom_oauth_client_secret, redirect_uri=s.zoom_oauth_redirect_uri, code=code,
            )
            me = zoom_oauth.fetch_me(api_base_url=s.zoom_api_base_url, access_token=tokens["access_token"])
        except zoom_oauth.ZoomError as e:
            logger.error("Zoom OAuth exchange failed: %s", e)
            return RedirectResponse(f"{done_url}&zoom_error=exchange_failed")
        get_store().save_zoom_connection(
            vexa_user_id=pending.vexa_user_id, zoom_user_id=me["id"], email=me["email"],
            vexa_token=pending.vexa_token, vexa_token_id=pending.vexa_token_id, **tokens,
        )
        return RedirectResponse(f"{done_url}&zoom_connected=1")

    @app.get("/zoom/status", name="zoom_status")
    def _status(vexa_user_id: str, request: Request) -> dict:
        _require_internal(request)
        s = get_settings()
        out = {"configured": _configured(), "webhook_ready": bool(s.zoom_webhook_secret_token)}
        connection = get_store().get_zoom_connection(vexa_user_id)
        if connection is None:
            return {**out, "connected": False}
        return {**out, "connected": True, "account_label": connection.email,
                "last_join": get_store().last_zoom_join(vexa_user_id)}

    @app.post("/zoom/disconnect", name="zoom_disconnect")
    def _disconnect(body: UserBody, request: Request) -> dict:
        """Forgets the connection and hands back the Vexa key it held, so the Terminal can revoke it."""
        _require_internal(request)
        s = get_settings()
        removed = get_store().delete_zoom_connection(body.vexa_user_id)
        if removed is not None:
            zoom_oauth.revoke(base_url=s.zoom_oauth_base_url, client_id=s.zoom_oauth_client_id,
                              client_secret=s.zoom_oauth_client_secret, token=removed.access_token)
        return {"connected": False, "vexa_token_id": removed.vexa_token_id if removed else None}

    @app.post("/webhooks/zoom", name="zoom_webhook")
    async def _webhook(request: Request, background_tasks: BackgroundTasks) -> dict:
        raw = await request.body()
        s = get_settings()
        try:
            event = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(status_code=400, detail="body is not JSON")
        if not isinstance(event, dict):
            raise HTTPException(status_code=400, detail="body is not an object")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}

        if event.get("event") == "endpoint.url_validation":
            try:
                return url_validation_response(secret_token=s.zoom_webhook_secret_token,
                                               plain_token=str(payload.get("plainToken") or ""))
            except ZoomSignatureError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
        try:
            verify_zoom_signature(
                secret_token=s.zoom_webhook_secret_token,
                timestamp=request.headers.get("x-zm-request-timestamp", ""),
                signature=request.headers.get("x-zm-signature", ""), body=raw,
            )
        except ZoomSignatureError as e:
            raise HTTPException(status_code=401, detail=str(e)) from e

        store = get_store()
        if event.get("event") == "meeting.started":
            meeting = payload.get("object") if isinstance(payload.get("object"), dict) else {}
            connection = store.get_zoom_connection_by_zoom_user(str(meeting.get("host_id") or ""))
            if connection is None or not meeting.get("id"):
                return {"ok": True, "ignored": "no connected host"}
            background_tasks.add_task(join_started_meeting, store=store, settings=s, connection=connection, meeting=meeting)
            return {"ok": True}
        if event.get("event") == "app_deauthorized":
            # The rep removed the app on Zoom's side: stop holding their tokens.
            zoom_user = store.get_zoom_connection_by_zoom_user(str(payload.get("user_id") or ""))
            if zoom_user is not None:
                store.delete_zoom_connection(zoom_user.vexa_user_id)
        return {"ok": True}
