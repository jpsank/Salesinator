"""Auto-detect the customer from calendar invites — no rep action needed.

When a bot joins a calendar-synced meeting, Vexa sends an alert that already includes who was
invited (their email addresses). We just read that straight off the alert — no extra lookup to Vexa
is needed. (Confirmed by actually triggering that alert during testing and reading the real thing it
sends, not by assuming from documentation — the documentation turned out to be wrong about this.)
"""

from __future__ import annotations

import logging

from sales_cycle.hubspot_client import HubSpotClient
from sales_cycle.resolver import WorkspaceBindError, bind_meeting_workspace, resolve_by_domain, slug_for_company

logger = logging.getLogger("sales_cycle.calendar_resolver")


def candidate_domains(attendees: list[dict], *, own_domains: set[str]) -> list[str]:
    """Pull out attendees' email domains, skip our own company's, and drop duplicates. Kept in the
    order attendees appear — the invited outside guest is usually listed first."""
    seen: list[str] = []
    for attendee in attendees:
        email = (attendee.get("email") or "").strip().lower()
        if "@" not in email:
            continue
        domain = email.rsplit("@", 1)[1]
        if domain and domain not in own_domains and domain not in seen:
            seen.append(domain)
    return seen


def resolve_meeting_started(
    *, event: dict, hubspot: HubSpotClient, gateway_url: str, api_key: str, own_domains: set[str],
) -> dict:
    """`event` is the "meeting" section of Vexa's alert. Returns whether we figured out the customer,
    and if so, which one. A miss (no attendees, or nobody matched in HubSpot) is normal and expected —
    it just means this call goes in the unmapped folder for a human to sort out later, not an error."""
    platform = event.get("platform")
    native_meeting_id = event.get("native_meeting_id")
    if not platform or not native_meeting_id:
        return {"resolved": False, "reason": "webhook payload missing platform/native_meeting_id"}

    attendees = ((event.get("data") or {}).get("attendees")) or []
    domains = candidate_domains(attendees, own_domains=own_domains)
    if not domains:
        return {"resolved": False, "reason": "no external attendee domain on this meeting"}

    for domain in domains:
        try:
            company = resolve_by_domain(hubspot, domain)
        except Exception:  # noqa: BLE001 — one bad lookup must not block trying the next domain
            logger.exception("HubSpot domain lookup failed for %r", domain)
            continue
        if company is not None:
            workspace_id = slug_for_company(company)
            try:
                bind_meeting_workspace(
                    gateway_url=gateway_url, api_key=api_key, platform=platform,
                    native_meeting_id=native_meeting_id, workspace_id=workspace_id,
                )
            except WorkspaceBindError as e:
                logger.error("calendar-path bind failed for %s/%s → %s: %s",
                             platform, native_meeting_id, workspace_id, e)
                return {"resolved": False, "reason": f"resolved {workspace_id} but binding failed: {e}"}
            return {"resolved": True, "workspace_id": workspace_id, "company_name": company.name}

    return {"resolved": False, "reason": f"no HubSpot company matched any attendee domain: {domains}"}
