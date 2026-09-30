import httpx
import respx

from conftest import GATEWAY, client


@respx.mock
def test_dispatch_without_tag_forwards_and_skips_resolution():
    respx.post(f"{GATEWAY}/bots").mock(
        return_value=httpx.Response(201, json={"id": 1, "platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    )
    resp = client.post(
        "/dispatch",
        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"},
        headers={"X-API-Key": "vxa_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bot"]["id"] == 1
    assert body["workspace"]["resolved"] is False


@respx.mock
def test_dispatch_with_tag_resolves_and_binds(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    respx.post(f"{GATEWAY}/bots").mock(
        return_value=httpx.Response(201, json={"id": 1, "platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    )
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "42", "properties": {"name": "Acme Corp", "domain": "acme.example.com"}},
        ]})
    )
    bind_route = respx.post(f"{GATEWAY}/meetings/google_meet/abc-defg-hij/workspace").mock(
        return_value=httpx.Response(200, json={"workspace_id": "cust-42"})
    )
    resp = client.post(
        "/dispatch",
        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "customer_tag": "Acme"},
        headers={"X-API-Key": "vxa_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace"] == {"resolved": True, "workspace_id": "cust-42", "company_name": "Acme Corp"}
    assert bind_route.called


@respx.mock
def test_dispatch_with_unresolvable_tag_does_not_fail_the_call(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    respx.post(f"{GATEWAY}/bots").mock(
        return_value=httpx.Response(201, json={"id": 1, "platform": "google_meet", "native_meeting_id": "abc-defg-hij"})
    )
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    resp = client.post(
        "/dispatch",
        json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij", "customer_tag": "Nonexistent"},
        headers={"X-API-Key": "vxa_test"},
    )
    assert resp.status_code == 200
    assert resp.json()["workspace"]["resolved"] is False


@respx.mock
def test_dispatch_forwards_bots_failure_verbatim():
    respx.post(f"{GATEWAY}/bots").mock(
        return_value=httpx.Response(503, json={"detail": "no transcription backend configured"})
    )
    resp = client.post(
        "/dispatch", json={"platform": "google_meet", "native_meeting_id": "abc-defg-hij"},
        headers={"X-API-Key": "vxa_test"},
    )
    assert resp.status_code == 503


@respx.mock
def test_tag_endpoint_resolves_and_binds(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "7", "properties": {"name": "Beta Corp", "domain": "beta.example.com"}},
        ]})
    )
    respx.post(f"{GATEWAY}/meetings/zoom/999/workspace").mock(
        return_value=httpx.Response(200, json={"workspace_id": "cust-7"})
    )
    resp = client.post(
        "/tag", json={"platform": "zoom", "native_meeting_id": "999", "customer_tag": "Beta"},
        headers={"X-API-Key": "vxa_test"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"workspace_id": "cust-7", "company_name": "Beta Corp"}


@respx.mock
def test_tag_endpoint_404s_on_unresolvable_company(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    resp = client.post(
        "/tag", json={"platform": "zoom", "native_meeting_id": "999", "customer_tag": "Nonexistent"},
        headers={"X-API-Key": "vxa_test"},
    )
    assert resp.status_code == 404
