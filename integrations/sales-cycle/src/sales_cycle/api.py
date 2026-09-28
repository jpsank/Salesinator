"""Every web address (endpoint) this add-on exposes, and what each one is for.

POST /dispatch
    Send a bot into a call, same as Vexa's own POST /bots, but also accepts a customer name
    (`customer_tag`) and tags the call with that customer before the bot joins.

POST /tag
    Tag a call that's already underway (or already sent) with a customer name — this is what the
    Slack `/vexa-tag` command calls.

POST /slack/events
    Slack sends things here: the one-time "prove you own this URL" handshake when you first set
    this up, and later, every time someone reacts to a message with an emoji.

POST /internal/process-approved
    For every approved feature request: if it hasn't been started yet, start the AI agent building
    it; if it's already building, check whether it's done, and if so, push it to GitHub.
    Also meant to run on a schedule.

POST /webhooks/meeting-started
    Vexa calls this the moment a bot joins a call. Used for the automatic version of customer
    tagging (see calendar_resolver.py) and starts this call's live feature-request watcher (see
    live_card_watcher.py) — the thing that posts each feature request to Slack the moment the
    copilot surfaces it, not after the call ends.

GET /oauth/{hubspot,slack}/authorize, .../callback, .../status, POST .../disconnect
    Each provider's whole "Connect X" flow — see oauth_routes.py (the shared 4-route shape),
    hubspot_oauth.py and slack_oauth.py (what's actually provider-specific). `authorize` sends the
    browser to the provider's consent screen; `callback` is where it sends the browser back with a
    code; `status`/`disconnect` back the Settings page's "Connected as ___ / Disconnect" display.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from sales_cycle import hubspot_oauth, slack_oauth
from sales_cycle.calendar_resolver import resolve_meeting_started
from sales_cycle.hubspot_client import HubSpotClient
from sales_cycle.live_card_watcher import watch_meeting
from sales_cycle.oauth_routes import OAuthProviderConfig, register_oauth_routes
from sales_cycle.orchestrator import DispatchError, PushError, git_state, push_if_ready, submit_implementation
from sales_cycle.resolver import WorkspaceBindError, bind_meeting_workspace, resolve_by_tag, slug_for_company
from sales_cycle.settings import Settings, get_settings
from sales_cycle.slack_client import SlackClient
from sales_cycle.slack_verify import SlackSignatureError, verify_slack_signature
from sales_cycle.store import PendingApproval, Store
from sales_cycle.webhook_verify import WebhookSignatureError, verify_webhook_signature

logger = logging.getLogger("sales_cycle.api")

app = FastAPI(title="vexa-sales-cycle")

_store: Store | None = None


def get_store() -> Store:
    """Lazy singleton so tests can point SALES_CYCLE_DB_PATH at a temp file before first use."""
    global _store
    if _store is None:
        _store = Store(get_settings().db_path)
    return _store


class DispatchRequest(BaseModel):
    platform: str | None = None
    native_meeting_id: str | None = None
    meeting_url: str | None = None
    bot_name: str | None = None
    language: str | None = None
    task: str | None = None
    transcribe_enabled: bool | None = None
    recording_enabled: bool | None = None
    customer_tag: str | None = None


class TagRequest(BaseModel):
    platform: str
    native_meeting_id: str
    customer_tag: str


class TagResponse(BaseModel):
    workspace_id: str
    company_name: str


def _hubspot() -> HubSpotClient:
    """Prefers a real "Connect HubSpot" OAuth connection; falls back to a hand-set static token
    (`SALES_CYCLE_HUBSPOT_TOKEN`) if HubSpot was never connected that way — so an operator who just
    wants to test this with their own private-app token doesn't need to go through OAuth at all."""
    s = get_settings()
    oauth_token = hubspot_oauth.get_valid_access_token(
        store=get_store(), client_id=s.hubspot_oauth_client_id, client_secret=s.hubspot_oauth_client_secret,
    )
    return HubSpotClient(token=oauth_token or s.hubspot_token, base_url=s.hubspot_base_url)


def _slack() -> SlackClient:
    """Same pick-one as HubSpot: prefers "Connect Slack", falls back to a hand-set bot token."""
    s = get_settings()
    oauth_token = slack_oauth.get_access_token(store=get_store())
    return SlackClient(bot_token=oauth_token or s.slack_bot_token)


def _resolve_and_bind(*, api_key: str, platform: str, native_meeting_id: str, customer_tag: str) -> TagResponse:
    company = resolve_by_tag(_hubspot(), customer_tag)
    if company is None:
        raise HTTPException(status_code=404, detail=f"no HubSpot company matched customer_tag={customer_tag!r}")
    workspace_id = slug_for_company(company)
    settings = get_settings()
    try:
        bind_meeting_workspace(
            gateway_url=settings.vexa_gateway_url, api_key=api_key,
            platform=platform, native_meeting_id=native_meeting_id, workspace_id=workspace_id,
        )
    except WorkspaceBindError as e:
        # The meeting/bot is real and running regardless — a failed bind is a triage item, not a
        # reason to fail the caller's request (P18: report, don't swallow, but don't block either).
        logger.error("workspace bind failed for %s/%s → %s: %s", platform, native_meeting_id, workspace_id, e)
        raise HTTPException(status_code=502, detail=f"resolved {workspace_id} but binding failed: {e}") from e
    return TagResponse(workspace_id=workspace_id, company_name=company.name)


@app.post("/tag", response_model=TagResponse)
def tag(body: TagRequest, x_api_key: str = Header(..., alias="X-API-Key")) -> TagResponse:
    return _resolve_and_bind(
        api_key=x_api_key, platform=body.platform, native_meeting_id=body.native_meeting_id,
        customer_tag=body.customer_tag,
    )


@app.post("/dispatch")
def dispatch(body: DispatchRequest, x_api_key: str = Header(..., alias="X-API-Key")) -> dict:
    settings = get_settings()
    forward = body.model_dump(exclude={"customer_tag"}, exclude_none=True)
    try:
        resp = httpx.post(
            f"{settings.vexa_gateway_url.rstrip('/')}/bots", json=forward,
            headers={"X-API-Key": x_api_key, "Content-Type": "application/json"}, timeout=10.0,
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"POST /bots unreachable: {type(e).__name__}: {e}") from e
    if resp.status_code >= 400:
        # Forward Vexa's own error verbatim — this wrapper adds a step, it doesn't hide failures.
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    result = resp.json()
    out: dict = {"bot": result}

    if not body.customer_tag:
        out["workspace"] = {"resolved": False, "reason": "no customer_tag given"}
        return out

    platform = result.get("platform") or body.platform
    native_meeting_id = result.get("native_meeting_id") or body.native_meeting_id
    if not platform or not native_meeting_id:
        out["workspace"] = {"resolved": False, "reason": "platform/native_meeting_id unavailable from response"}
        return out

    try:
        tagged = _resolve_and_bind(
            api_key=x_api_key, platform=platform, native_meeting_id=native_meeting_id,
            customer_tag=body.customer_tag,
        )
        out["workspace"] = {"resolved": True, **tagged.model_dump()}
    except HTTPException as e:
        # The bot is already dispatched — a resolution miss is never a reason to fail the whole call.
        out["workspace"] = {"resolved": False, "reason": e.detail}
    return out


def _dispatch_one(store: Store, settings: Settings, approval: PendingApproval) -> bool:
    """Starts the implementation turn for ONE approved request. Shared by the real-time path (fires
    the moment the ✅ reaction arrives) and the cron sweep (a safety net for anything that path
    missed — e.g. this service was down when the reaction came in). `claim_for_dispatch` makes the
    two paths race-safe: only one of them can ever start a turn for a given approval, since a
    duplicate turn would mean two agents mutating the SAME shared product-repo workspace at once."""
    if not store.claim_for_dispatch(approval.id):
        return False  # the other path already claimed it — not an error, just a race we lost
    try:
        result = submit_implementation(
            agent_api_url=settings.agent_api_internal_url,
            subject=settings.product_repo_subject,
            title=approval.title, body=approval.body,
        )
    except DispatchError:
        logger.exception("dispatch failed for approval id=%s title=%r", approval.id, approval.title)
        store.revert_to_approved(approval.id)
        return False
    store.mark_dispatched(approval.id, branch=result["branch"], workload_id=result.get("workload_id"))
    return True


@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.body()
    settings = get_settings()
    try:
        verify_slack_signature(
            signing_secret=settings.slack_signing_secret,
            timestamp=request.headers.get("X-Slack-Request-Timestamp", ""),
            signature=request.headers.get("X-Slack-Signature", ""),
            body=body,
        )
    except SlackSignatureError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    payload = await request.json()

    # The one-time URL-verification handshake Slack does when you first register the endpoint.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    if payload.get("type") == "event_callback":
        event = payload.get("event") or {}
        if event.get("type") == "reaction_added" and event.get("reaction") in ("white_check_mark", "heavy_check_mark"):
            item = event.get("item") or {}
            store = get_store()
            approved = store.approve(
                slack_channel=item.get("channel", ""), slack_ts=item.get("ts", ""),
            )
            if approved is None:
                logger.info("reaction on %s/%s — not a pending feature-request message, or already approved",
                            item.get("channel"), item.get("ts"))
            else:
                logger.info("feature-request approved: workspace=%s title=%r", approved.workspace_id, approved.title)
                # Slack needs this ack within 3s — start the implementation turn AFTER responding,
                # not before (process-approved's cron sweep still catches it if this never runs).
                background_tasks.add_task(_dispatch_one, store, settings, approved)
    return {"ok": True}


@app.post("/internal/process-approved")
def process_approved() -> dict:
    settings = get_settings()
    store = get_store()
    dispatched_now = []
    pushed_now = []

    for approval in store.list_approved_unprocessed():
        if _dispatch_one(store, settings, approval):
            dispatched_now.append(approval.id)

    unpushed = store.list_dispatched_unpushed()
    if unpushed:
        # Every approval here shares the SAME product_repo_subject workspace — one git-state fetch
        # answers for all of them in this sweep, instead of one redundant identical GET per approval.
        try:
            state = git_state(agent_api_url=settings.agent_api_internal_url, subject=settings.product_repo_subject, timeout=15.0)
        except DispatchError:
            logger.exception("git-state fetch failed — skipping the push check for this sweep")
            state = None
        if state is not None:
            for approval in unpushed:
                try:
                    pushed = push_if_ready(
                        state, agent_api_url=settings.agent_api_internal_url,
                        subject=settings.product_repo_subject, expected_branch=approval.branch,
                    )
                except PushError:
                    logger.exception("push failed for approval id=%s branch=%s", approval.id, approval.branch)
                    continue
                if pushed is not None:
                    store.mark_done(approval.id)
                    pushed_now.append(approval.id)

    return {"dispatched": dispatched_now, "pushed": pushed_now}


@app.post("/webhooks/meeting-started")
async def webhook_meeting_started(request: Request, background_tasks: BackgroundTasks) -> dict:
    body = await request.body()
    settings = get_settings()
    try:
        verify_webhook_signature(
            secret=settings.calendar_webhook_secret,
            timestamp=request.headers.get("X-Webhook-Timestamp", ""),
            signature=request.headers.get("X-Webhook-Signature", ""),
            body=body,
        )
    except WebhookSignatureError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e

    payload = await request.json()
    if payload.get("event_type") != "meeting.started":
        return {"resolved": False, "reason": "not a meeting.started event"}

    meeting = ((payload.get("data") or {}).get("meeting")) or {}

    meeting_id = meeting.get("id")
    subject = meeting.get("user_id")
    if meeting_id is not None and subject is not None:
        background_tasks.add_task(
            watch_meeting,
            agent_api_url=settings.agent_api_internal_url, meeting_api_url=settings.meeting_api_internal_url,
            subject=str(subject), meeting_id=str(meeting_id), store=get_store(), slack=_slack(),
            channel=settings.slack_channel_id, unmapped_slug=settings.unmapped_workspace_slug,
        )
    else:
        logger.warning("meeting.started payload missing meeting.id/user_id — no live watcher started")

    own_domains = {d.strip().lower() for d in settings.own_domains.split(",") if d.strip()}
    result = resolve_meeting_started(
        event=meeting, hubspot=_hubspot(), gateway_url=settings.vexa_gateway_url,
        api_key=settings.calendar_api_key, own_domains=own_domains,
    )
    if not result["resolved"]:
        logger.info("calendar-path resolution miss: %s", result.get("reason"))
    return result


def _hubspot_oauth_config() -> OAuthProviderConfig:
    s = get_settings()
    return OAuthProviderConfig(
        client_id=s.hubspot_oauth_client_id, client_secret=s.hubspot_oauth_client_secret,
        redirect_uri=s.hubspot_oauth_redirect_uri, scopes=s.hubspot_oauth_scopes,
    )


def _slack_oauth_config() -> OAuthProviderConfig:
    s = get_settings()
    return OAuthProviderConfig(
        client_id=s.slack_oauth_client_id, client_secret=s.slack_oauth_client_secret,
        redirect_uri=s.slack_oauth_redirect_uri, scopes=s.slack_oauth_scopes,
    )


register_oauth_routes(
    app, provider="hubspot",
    build_authorize_url=hubspot_oauth.build_authorize_url, exchange_code=hubspot_oauth.exchange_code,
    error_cls=hubspot_oauth.HubSpotOAuthError, get_store=get_store, get_config=_hubspot_oauth_config,
)

register_oauth_routes(
    app, provider="slack",
    build_authorize_url=slack_oauth.build_authorize_url, exchange_code=slack_oauth.exchange_code,
    error_cls=slack_oauth.SlackOAuthError, get_store=get_store, get_config=_slack_oauth_config,
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "sales-cycle"}
