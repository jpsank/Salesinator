"""Resolve a customer_tag (or, later, an attendee email domain) to a workspace slug via HubSpot, and
bind it onto a meeting via Vexa's own existing `POST /meetings/{platform}/{native_meeting_id}/workspace`
endpoint (docs/docs/api/meetings.mdx:164-174) — no bespoke storage; the meeting record's `workspace_id`
(P23 single-writer) is the one place this lives.
"""

from __future__ import annotations

import logging

import httpx

from sales_cycle.hubspot_client import Company, HubSpotClient, HubSpotError

logger = logging.getLogger("sales_cycle.resolver")


class WorkspaceBindError(RuntimeError):
    """Binding the meeting's workspace_id failed — caller decides whether to surface or swallow."""


def slug_for_company(company: Company) -> str:
    """Stable slug keyed on the HubSpot company id, not its name/domain — survives a rename."""
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
    """Calls the EXISTING binding endpoint — never invents a parallel notion of 'which workspace'."""
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
