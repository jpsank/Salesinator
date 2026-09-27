"""The sales-cycle service's public surface (P6 — one front door).

POST /dispatch — wraps POST /bots: forwards the dispatch unchanged, then (if a customer_tag was
  given) resolves it against HubSpot and binds the resulting workspace onto the new meeting via
  Vexa's existing workspace-binding endpoint. Never blocks the bot from joining on a resolution miss.

POST /tag — resolve-or-bind for an ALREADY-DISPATCHED meeting (the Slack-slash-command path: a rep
  tags a call after sending it, or before the copilot arms).
"""

from __future__ import annotations

import logging

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from sales_cycle.hubspot_client import HubSpotClient
from sales_cycle.resolver import WorkspaceBindError, bind_meeting_workspace, resolve_by_tag, slug_for_company
from sales_cycle.settings import get_settings

logger = logging.getLogger("sales_cycle.api")

app = FastAPI(title="vexa-sales-cycle")


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


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "sales-cycle"}
