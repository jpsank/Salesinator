"""Looks up a company in HubSpot — by name, or by email domain.

We talk to HubSpot's web API directly with plain requests, rather than installing HubSpot's own
software package for it — it's only two lookups, and this keeps things simple. Everywhere else in
this add-on works with the clean `Company` object below, never HubSpot's own raw response format.
"""

from __future__ import annotations

from dataclasses import dataclass

from sales_cycle._http import call


@dataclass(frozen=True)
class Company:
    id: str
    name: str
    domain: str | None


class HubSpotError(RuntimeError):
    """A HubSpot call failed — the caller decides whether that's fatal or falls back to unmapped."""


class HubSpotClient:
    def __init__(self, *, token: str, base_url: str = "https://api.hubapi.com", timeout: float = 5.0):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _search(self, *, property_name: str, operator: str, value: str) -> Company | None:
        if not self._token:
            raise HubSpotError("HUBSPOT_TOKEN not configured")
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": property_name, "operator": operator, "value": value},
            ]}],
            "properties": ["name", "domain"],
            "limit": 1,
        }
        resp = call(
            "POST", f"{self._base_url}/crm/v3/objects/companies/search",
            json=body, headers={"Authorization": f"Bearer {self._token}"}, timeout=self._timeout,
            error_cls=HubSpotError, error_prefix="HubSpot search",
        )
        results = resp.json().get("results") or []
        if not results:
            return None
        hit = results[0]
        props = hit.get("properties") or {}
        return Company(id=str(hit["id"]), name=props.get("name") or "", domain=props.get("domain"))

    def find_by_name(self, name: str) -> Company | None:
        """A loose, partial-match search — for when a rep types a company name in Slack."""
        return self._search(property_name="name", operator="CONTAINS_TOKEN", value=name)

    def find_by_domain(self, domain: str) -> Company | None:
        """An exact match — for auto-detecting the customer from a calendar invite's email domain."""
        return self._search(property_name="domain", operator="EQ", value=domain)
