"""The sales-cycle service's public surface (P6 — one front door).

POST /dispatch — wraps POST /bots: forwards the dispatch unchanged, then (if a customer_tag was
  given) resolves it against HubSpot and binds the resulting workspace onto the new meeting via
  Vexa's existing workspace-binding endpoint. Never blocks the bot from joining on a resolution miss.

POST /tag — resolve-or-bind for an ALREADY-DISPATCHED meeting (the Slack-slash-command path: a rep
  tags a call after sending it, or before the copilot arms).

POST /internal/poll-feature-requests — sweep the workspaces volume for new feature_request entities,
  post one Slack message per new one (cron-triggered; no in-process scheduler in v1).

POST /slack/events — Slack Events API receiver: URL verification handshake, and a ✅ reaction on a
  feature-request message marks it approved.

POST /internal/process-approved — for each approved-but-not-yet-dispatched request, fire the
  implementation turn; for each already-dispatched one, check whether it finished and push it.
  Cron-triggered, same pattern as the feature-request poller.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from sales_cycle.hubspot_client import HubSpotClient
from sales_cycle.orchestrator import DispatchError, PushError, check_and_push, submit_implementation
from sales_cycle.poller import poll_once
from sales_cycle.resolver import WorkspaceBindError, bind_meeting_workspace, resolve_by_tag, slug_for_company
from sales_cycle.settings import get_settings
from sales_cycle.slack_client import SlackClient
from sales_cycle.slack_verify import SlackSignatureError, verify_slack_signature
from sales_cycle.store import Store

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
    s = get_settings()
    return HubSpotClient(token=s.hubspot_token, base_url=s.hubspot_base_url)


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


@app.post("/internal/poll-feature-requests")
def poll_feature_requests() -> dict:
    settings = get_settings()
    slack = SlackClient(bot_token=settings.slack_bot_token)
    notified = poll_once(
        store=get_store(), slack=slack, workspaces_root=Path(settings.workspaces_root),
        channel=settings.slack_channel_id,
    )
    return {"notified": notified, "count": len(notified)}


@app.post("/slack/events")
async def slack_events(request: Request) -> dict:
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
            approved = get_store().approve(
                slack_channel=item.get("channel", ""), slack_ts=item.get("ts", ""),
            )
            if approved is None:
                logger.info("reaction on %s/%s — not a pending feature-request message, or already approved",
                            item.get("channel"), item.get("ts"))
            else:
                logger.info("feature-request approved: workspace=%s title=%r", approved.workspace_id, approved.title)
    return {"ok": True}


@app.post("/internal/process-approved")
def process_approved() -> dict:
    settings = get_settings()
    store = get_store()
    dispatched_now = []
    pushed_now = []

    for approval in store.list_approved_unprocessed():
        try:
            result = submit_implementation(
                agent_api_url=settings.agent_api_internal_url,
                user_id=settings.product_repo_user_id,
                title=approval.title, body=approval.body,
            )
        except DispatchError:
            logger.exception("dispatch failed for approval id=%s title=%r", approval.id, approval.title)
            continue
        store.mark_dispatched(approval.id, branch=result["branch"], workload_id=result.get("workload_id"))
        dispatched_now.append(approval.id)

    for approval in store.list_dispatched_unpushed():
        try:
            pushed = check_and_push(
                agent_api_url=settings.agent_api_internal_url,
                user_id=settings.product_repo_user_id,
                expected_branch=approval.branch,
            )
        except PushError:
            logger.exception("push failed for approval id=%s branch=%s", approval.id, approval.branch)
            continue
        if pushed is not None:
            store.mark_done(approval.id)
            pushed_now.append(approval.id)

    return {"dispatched": dispatched_now, "pushed": pushed_now}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "sales-cycle"}
