"""Turns a customer name (or email domain) into a Vexa workspace, using HubSpot to identify the
company, then tags the meeting with it using Vexa's own existing "bind this meeting to a workspace"
endpoint. We don't keep our own separate record of which meeting belongs to which customer — Vexa's
own meeting record is the one place that's tracked.
"""

from __future__ import annotations

import logging

import httpx

from sales_cycle.hubspot_client import Company, HubSpotClient, HubSpotError

logger = logging.getLogger("sales_cycle.resolver")


class WorkspaceBindError(RuntimeError):
    """Binding the meeting's workspace_id failed — caller decides whether to surface or swallow."""


def slug_for_company(company: Company) -> str:
    """The workspace name we use for this company — based on its HubSpot ID, not its name, so it
    doesn't break if the company gets renamed in HubSpot later."""
    return f"cust-{company.id}"


def resolve_by_tag(hubspot: HubSpotClient, customer_tag: str) -> Company | None:
    try:
        return hubspot.find_by_name(customer_tag)
    except HubSpotError:
        logger.exception("HubSpot lookup failed for customer_tag=%r", customer_tag)
        return None


def resolve_by_domain(hubspot: HubSpotClient, domain: str) -> Company | None:
    try:
        return hubspot.find_by_domain(domain)
    except HubSpotError:
        logger.exception("HubSpot lookup failed for domain=%r", domain)
        return None


def bind_meeting_workspace(
    *, gateway_url: str, api_key: str, platform: str, native_meeting_id: str, workspace_id: str,
    timeout: float = 5.0,
) -> None:
    """Tags the meeting with the given workspace, using Vexa's own endpoint for it."""
    url = f"{gateway_url.rstrip('/')}/meetings/{platform}/{native_meeting_id}/workspace"
    try:
        resp = httpx.post(
            url, json={"workspace_id": workspace_id},
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise WorkspaceBindError(f"POST {url} failed: {type(e).__name__}: {e}") from e
