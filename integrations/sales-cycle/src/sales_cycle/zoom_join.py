"""Send Vexa's bot to a Zoom meeting a connected rep just started.

The bot is sent exactly the way a rep's own "add bot from URL" sends it — through Vexa's gateway with the
rep's own bot-scoped key — so quotas, the rep's webhooks (which is what starts the live feature-request
capture), recording defaults and the "one bot per meeting" guard all behave as for any manual join.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

from sales_cycle import zoom_oauth
from sales_cycle.settings import Settings
from sales_cycle.store import Store, ZoomConnection

logger = logging.getLogger("sales_cycle.zoom_join")

# A meeting that has only just started may not be readable through the API for a moment.
MEETING_LOOKUP_ATTEMPTS = 3
MEETING_LOOKUP_DELAY_SEC = 2.0


def _join_url(settings: Settings, store: Store, connection: ZoomConnection, meeting_id: str,
              sleep: Callable[[float], None]) -> str:
    """The meeting's own join link (it carries the passcode). When Zoom will not give it up, fall back to the
    bare link — a meeting without a passcode still admits the bot, and one with one fails loudly at join time."""
    last: Exception | None = None
    for attempt in range(MEETING_LOOKUP_ATTEMPTS):
        try:
            token = zoom_oauth.get_valid_access_token(
                store=store, connection=connection, base_url=settings.zoom_oauth_base_url,
                client_id=settings.zoom_oauth_client_id, client_secret=settings.zoom_oauth_client_secret,
            )
            meeting = zoom_oauth.get_meeting(api_base_url=settings.zoom_api_base_url, access_token=token, meeting_id=meeting_id)
            if meeting.get("join_url"):
                return str(meeting["join_url"])
        except zoom_oauth.ZoomError as e:
            last = e
        if attempt + 1 < MEETING_LOOKUP_ATTEMPTS:
            sleep(MEETING_LOOKUP_DELAY_SEC)
    logger.warning("zoom meeting %s: no join_url from the API (%s) — using the bare link", meeting_id, last)
    return f"https://zoom.us/j/{meeting_id}"


def _is_concurrency_refusal(resp: httpx.Response) -> bool:
    """Vexa answers a spawn past the concurrency limit with a 403 whose ``detail.reason`` says so (documented as not
    yet matching its 429 contract) — a quota to back off from, not a rejected key."""
    if resp.status_code != 403:
        return False
    try:
        detail = resp.json().get("detail")
    except ValueError:
        return False
    return isinstance(detail, dict) and detail.get("reason") == "concurrency_limit_reached"


def join_started_meeting(
    *, store: Store, settings: Settings, connection: ZoomConnection, meeting: dict,
    sleep: Callable[[float], None] = time.sleep, client: httpx.Client | None = None,
) -> str:
    """Returns the outcome, which is also recorded: ``joined`` · ``already_joined`` · ``limit_reached`` ·
    ``vexa_key_rejected`` · ``failed`` (``duplicate`` — a redelivered notification — is returned but not recorded)."""
    uuid = str(meeting.get("uuid") or meeting.get("id"))
    meeting_id = str(meeting.get("id") or "")
    if not store.claim_zoom_join(
        meeting_uuid=uuid, vexa_user_id=connection.vexa_user_id, zoom_meeting_id=meeting_id, topic=meeting.get("topic"),
    ):
        return "duplicate"          # Zoom redelivered the notification — the first one already sent the bot
    outcome, detail = "failed", None
    try:
        url = _join_url(settings, store, connection, meeting_id, sleep)
        owns_client = client is None
        client = client or httpx.Client(timeout=15.0)
        try:
            resp = client.post(
                f"{settings.vexa_gateway_url.rstrip('/')}/bots", json={"meeting_url": url},
                headers={"X-API-Key": connection.vexa_token, "Content-Type": "application/json"},
            )
        finally:
            if owns_client:
                client.close()
        if resp.status_code in (200, 201):
            outcome = "joined"
        elif resp.status_code == 409:
            outcome = "already_joined"      # a calendar auto-join or a manual add got there first
        elif resp.status_code == 401:
            outcome, detail = "vexa_key_rejected", "Vexa rejected the key this connection sends the bot with — reconnect Zoom"
        elif resp.status_code == 429 or _is_concurrency_refusal(resp):
            outcome, detail = "limit_reached", "the rep is at their limit of concurrent bots"
        else:
            detail = f"Vexa answered {resp.status_code}: {resp.text[:200]}"
    except (httpx.HTTPError, zoom_oauth.ZoomError) as e:
        detail = f"{type(e).__name__}: {e}"
    if outcome in ("failed", "vexa_key_rejected", "limit_reached"):
        logger.warning("zoom auto-join for vexa user %s, meeting %s: %s — %s", connection.vexa_user_id, meeting_id, outcome, detail)
    store.record_zoom_join_outcome(uuid, outcome=outcome, detail=detail)
    return outcome
