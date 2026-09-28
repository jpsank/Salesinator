import httpx
import respx

from sales_cycle.calendar_resolver import candidate_domains, resolve_meeting_started
from sales_cycle.hubspot_client import HubSpotClient

GATEWAY = "http://gateway:8000"


def test_candidate_domains_strips_own_domain_and_dedupes():
    attendees = [
        {"email": "sarah@acme.example.com"},
        {"email": "me@ourcompany.com"},
        {"email": "bob@acme.example.com"},
    ]
    assert candidate_domains(attendees, own_domains={"ourcompany.com"}) == ["acme.example.com"]


def test_candidate_domains_ignores_malformed_entries():
    attendees = [{"email": ""}, {"name": "no email field"}, {"email": "not-an-email"}]
    assert candidate_domains(attendees, own_domains=set()) == []


@respx.mock
def test_resolve_meeting_started_success():
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "42", "properties": {"name": "Acme Corp", "domain": "acme.example.com"}},
        ]})
    )
    bind_route = respx.post(f"{GATEWAY}/meetings/google_meet/abc-defg-hij/workspace").mock(
        return_value=httpx.Response(200, json={"workspace_id": "cust-42"})
    )
    event = {
        "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "data": {"attendees": [{"email": "sarah@acme.example.com"}]},
    }
    result = resolve_meeting_started(
        event=event, hubspot=HubSpotClient(token="tok"), gateway_url=GATEWAY, api_key="vxa_test",
        own_domains=set(),
    )
    assert result == {"resolved": True, "workspace_id": "cust-42", "company_name": "Acme Corp"}
    assert bind_route.called


@respx.mock
def test_resolve_meeting_started_no_attendees():
    event = {"platform": "google_meet", "native_meeting_id": "abc", "data": {}}
    result = resolve_meeting_started(
        event=event, hubspot=HubSpotClient(token="tok"), gateway_url=GATEWAY, api_key="vxa_test",
        own_domains=set(),
    )
    assert result["resolved"] is False
    assert "no external attendee domain" in result["reason"]


@respx.mock
def test_resolve_meeting_started_no_hubspot_match_tries_all_domains_then_gives_up():
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    event = {
        "platform": "zoom", "native_meeting_id": "999",
        "data": {"attendees": [{"email": "a@one.example.com"}, {"email": "b@two.example.com"}]},
    }
    result = resolve_meeting_started(
        event=event, hubspot=HubSpotClient(token="tok"), gateway_url=GATEWAY, api_key="vxa_test",
        own_domains=set(),
    )
    assert result["resolved"] is False
    assert "one.example.com" in result["reason"] and "two.example.com" in result["reason"]


def test_resolve_meeting_started_missing_ids():
    result = resolve_meeting_started(
        event={"data": {}}, hubspot=HubSpotClient(token="tok"), gateway_url=GATEWAY, api_key="k",
        own_domains=set(),
    )
    assert result["resolved"] is False
    assert "platform/native_meeting_id" in result["reason"]


@respx.mock
def test_resolve_meeting_started_bind_failure_reported_not_raised():
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "1", "properties": {"name": "X", "domain": "x.com"}},
        ]})
    )
    respx.post(f"{GATEWAY}/meetings/zoom/1/workspace").mock(return_value=httpx.Response(502))
    event = {"platform": "zoom", "native_meeting_id": "1", "data": {"attendees": [{"email": "a@x.com"}]}}
    result = resolve_meeting_started(
        event=event, hubspot=HubSpotClient(token="tok"), gateway_url=GATEWAY, api_key="k", own_domains=set(),
    )
    assert result["resolved"] is False
    assert "binding failed" in result["reason"]
