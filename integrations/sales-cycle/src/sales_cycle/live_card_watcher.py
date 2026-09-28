"""Watches ONE live meeting's copilot cards in real time and posts each `feature_request` straight to
Slack the moment it appears — the actual fix for the pipeline's core promise (spin up a live preview
"in the same call" the customer asked for it), not just a latency tweak on top of something that
worked. It replaces the old poll-based mechanism (`poller.py` + `entity_files.py`), which read
markdown files under `kg/entities/feature_request/*.md` — files nothing in the pipeline has ever
written; the only markdown write in the whole meeting flow is the ONE combined session-end doc, so
that mechanism never actually captured anything from a real call.

The copilot's live output — including `feature_request` cards — already flows live, per beat, onto
agent-api's per-meeting SSE feed (`GET /api/meeting/stream`), the same feed the terminal's live-call
view renders from. This module just tails that feed for the meeting's whole duration instead of
polling a file, and is launched once per call, as a background task, straight off the
`meeting.started` webhook (see `api.py`).

Known limitation: launched via FastAPI's `BackgroundTasks`, which is fire-and-forget — unlike
`/internal/process-approved`'s cron sweep (a deliberately self-healing safety net for the dispatch/
push leg), nothing here notices or restarts a watcher lost to a sales-cycle restart mid-call. A
lost watcher costs that one call its live capture; nothing else. Giving this leg the same
self-healing shape as the dispatch leg (recording "watcher active for meeting X" in `store` so a
sweep can detect and restart a missed one) is a reasonable next step, not done here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from sales_cycle.slack_client import SlackClient, SlackError
from sales_cycle.store import Store

logger = logging.getLogger("sales_cycle.live_card_watcher")

_RECONNECT_DELAY_SEC = 3.0
# A dropped connection this many times in a row means something's structurally wrong (not a blip) —
# stop retrying rather than hammering agent-api for the rest of a long call.
_MAX_RECONNECTS = 20
# Bounds how stale a card's workspace routing can be after a mid-call tag/bind — long enough that a
# burst of cards seconds apart shares one lookup, short enough that nobody notices the delay.
_WORKSPACE_CACHE_TTL_SEC = 5.0


def _format_message(workspace_id: str, title: str, body: str) -> str:
    return (
        f":bulb: *Feature request* — `{workspace_id}`\n"
        f"*{title}*\n"
        f"{body}\n\n"
        f"React :white_check_mark: to approve — an agent will implement it on a branch and push it."
    )


async def _resolve_workspace_id(
    *, client: httpx.AsyncClient, meeting_api_url: str, subject: str, meeting_id: str, unmapped_slug: str,
) -> str:
    """Reads the meeting's workspace binding — NOT cached here (the caller applies a short TTL cache;
    see `_CachedWorkspaceResolver` below) because a manual /tag (or the calendar resolver) can land
    after the call, and its cards, already started. Falls back to the unmapped slug on any miss: the
    request is never lost, just routed to a human to sort out, same stance as every other resolver in
    this add-on."""
    try:
        resp = await client.get(
            f"{meeting_api_url.rstrip('/')}/meetings/{meeting_id}",
            headers={"X-User-Id": subject}, timeout=5.0,
        )
        resp.raise_for_status()
        workspace_id = ((resp.json().get("data") or {}).get("workspace_id") or "").strip()
    except (httpx.HTTPError, ValueError):
        logger.exception("workspace lookup failed for meeting_id=%s — routing to %s", meeting_id, unmapped_slug)
        return unmapped_slug
    return workspace_id or unmapped_slug


class _CachedWorkspaceResolver:
    """One meeting's workspace binding rarely changes mid-call, but the FIRST card in a fresh binding
    (a tag/bind landing after the call, and its cards, already started) must see it fresh — so this
    caches for `_WORKSPACE_CACHE_TTL_SEC`, not forever, bounding a burst of cards to one lookup
    without holding a stale answer indefinitely."""

    def __init__(self, *, client: httpx.AsyncClient, meeting_api_url: str, subject: str, meeting_id: str, unmapped_slug: str):
        self._client = client
        self._meeting_api_url = meeting_api_url
        self._subject = subject
        self._meeting_id = meeting_id
        self._unmapped_slug = unmapped_slug
        self._value: str | None = None
        self._fetched_at = 0.0

    async def get(self) -> str:
        now = time.monotonic()
        if self._value is None or (now - self._fetched_at) >= _WORKSPACE_CACHE_TTL_SEC:
            self._value = await _resolve_workspace_id(
                client=self._client, meeting_api_url=self._meeting_api_url, subject=self._subject,
                meeting_id=self._meeting_id, unmapped_slug=self._unmapped_slug,
            )
            self._fetched_at = now
        return self._value


async def _sse_events(response: httpx.Response):
    """Yield one parsed JSON event per SSE frame: one-or-more `data:` lines ended by a blank line
    (the actual wire spec — a single-line `data: {...}` frame is just the common case of it). Same
    framing rule as `clients/slim/vexa_slim/sse.py`'s `read_sse_events` (that package isn't wired as
    a runtime dependency of this add-on, so this mirrors its algorithm rather than importing it)."""
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip(" "))
        elif line == "":  # blank line = end of one frame
            if data_lines:
                try:
                    yield json.loads("".join(data_lines))
                except json.JSONDecodeError:
                    pass
                data_lines = []
        # `id:` lines and `: keepalive` comments carry no data — silently skipped either way.


async def _feature_request_cards(client: httpx.AsyncClient, url: str, subject: str):
    """Yields each `feature_request` card's (title, body) as it arrives on the SSE feed, live. Returns
    once the feed itself reports the meeting has ended. Reconnects transparently on a dropped
    connection — a fresh connect replays the feed's recent output backlog, so it's the caller's
    dedup (not an SSE resume cursor) that keeps a reconnect from posting the same card twice.

    A connection that opens but answers with an error status (403 not authorized, 404 unknown
    meeting) is NOT retried — that's a permanent rejection, not a blip, and retrying it 20 times
    would just waste a minute hammering agent-api for the same answer; it propagates to the
    caller's own exception handling instead."""
    reconnects = 0
    while True:
        try:
            async with client.stream(
                "GET", url, headers={"X-User-Id": subject},
                timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
            ) as resp:
                resp.raise_for_status()
                reconnects = 0
                async for event in _sse_events(resp):
                    if event.get("type") == "meeting-end":
                        return
                    if event.get("type") != "card":
                        continue
                    card = event.get("card") or {}
                    if card.get("kind") == "feature_request" and card.get("title"):
                        yield str(card["title"]), str(card.get("body") or "")
        except httpx.TransportError:
            reconnects += 1
            if reconnects > _MAX_RECONNECTS:
                logger.error("live card stream giving up after %d reconnects: %s", reconnects, url)
                return
            logger.warning("live card stream dropped (attempt %d/%d) — reconnecting", reconnects, _MAX_RECONNECTS)
            await asyncio.sleep(_RECONNECT_DELAY_SEC)


async def watch_meeting(
    *, agent_api_url: str, meeting_api_url: str, subject: str, meeting_id: str,
    store: Store, slack: SlackClient, channel: str, unmapped_slug: str,
) -> None:
    """Tails this one meeting's copilot output for its whole duration and posts each `feature_request`
    card to Slack the instant it appears — not at session end, not on a 60s sweep. `subject` is the
    meeting's OWNER (its dispatching Vexa user, from the webhook's `data.meeting.user_id`) — that's
    what both agent-api's SSE ownership check and meeting-api's own records are keyed on; it is NOT
    the customer workspace, which is looked up per card (through a short-TTL cache — see
    `_CachedWorkspaceResolver`) since it can bind mid-call.

    Never raises — a background task that dies silently just costs this one meeting its live capture
    (the call itself, and everything else in the process, keeps going regardless); the broad
    exception handling below is deliberate, not sloppy (P18: report, don't propagate into whatever
    launched us)."""
    url = f"{agent_api_url.rstrip('/')}/api/meeting/stream?meeting_id={meeting_id}&session_uid={meeting_id}"
    dedupe_prefix = f"live:{meeting_id}:"
    try:
        async with httpx.AsyncClient() as client:
            workspace = _CachedWorkspaceResolver(
                client=client, meeting_api_url=meeting_api_url, subject=subject,
                meeting_id=meeting_id, unmapped_slug=unmapped_slug,
            )
            async for title, body in _feature_request_cards(client, url, subject):
                key = f"{dedupe_prefix}{title.strip().casefold()}"
                # Sqlite calls below run directly, not through asyncio.to_thread: they're local,
                # sub-millisecond, and fire at most once per distinct card (not a hot path) — an
                # executor round-trip would cost more than the blocking call it avoids.
                if store.is_seen(key):
                    continue
                workspace_id = await workspace.get()
                try:
                    ts = await asyncio.to_thread(
                        slack.post_message, channel=channel, text=_format_message(workspace_id, title, body),
                    )
                except SlackError:
                    logger.exception(
                        "Slack post failed for live card %r on meeting_id=%s — not marked seen, "
                        "will retry if the copilot re-surfaces it", title, meeting_id,
                    )
                    continue
                store.record_pending_approval(
                    slack_channel=channel, slack_ts=ts, workspace_id=workspace_id, source_key=key,
                    title=title, body=body,
                )
                store.mark_seen(key)
    except Exception:  # noqa: BLE001 — a background task must never crash the process (P18)
        logger.exception("live card watcher crashed for meeting_id=%s", meeting_id)
