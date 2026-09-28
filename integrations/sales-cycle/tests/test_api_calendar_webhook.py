import hashlib
import hmac
import json
import time

import httpx
import respx
from fastapi.testclient import TestClient

from sales_cycle.api import app

client = TestClient(app)

SECRET = "test-webhook-secret"
GATEWAY = "http://gateway:8000"


def _sign(timestamp: str, body: bytes) -> str:
    basestring = f"{timestamp}.{body.decode()}".encode()
    return "sha256=" + hmac.new(SECRET.encode(), basestring, hashlib.sha256).hexdigest()


def _post_webhook(payload: dict):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    return client.post(
        "/webhooks/meeting-started", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": ts,
            "X-Webhook-Signature": _sign(ts, body),
        },
    )


def _meeting_started_payload(**meeting_overrides) -> dict:
    meeting = {
        "id": 1, "platform": "google_meet", "native_meeting_id": "abc-defg-hij",
        "status": "active", "data": {"attendees": [{"email": "sarah@acme.example.com"}]},
    }
    meeting.update(meeting_overrides)
    return {"event_type": "meeting.started", "data": {"meeting": meeting}}


@respx.mock
def test_meeting_started_resolves_and_binds(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_API_KEY", "vxa_rep_key")

    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "42", "properties": {"name": "Acme Corp", "domain": "acme.example.com"}},
        ]})
    )
    bind_route = respx.post(f"{GATEWAY}/meetings/google_meet/abc-defg-hij/workspace").mock(
        return_value=httpx.Response(200, json={"workspace_id": "cust-42"})
    )

    resp = _post_webhook(_meeting_started_payload())
    assert resp.status_code == 200
    assert resp.json() == {"resolved": True, "workspace_id": "cust-42", "company_name": "Acme Corp"}
    assert bind_route.called


def test_bad_signature_rejected(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    body = json.dumps(_meeting_started_payload()).encode()
    resp = client.post(
        "/webhooks/meeting-started", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Timestamp": str(int(time.time())),
            "X-Webhook-Signature": "sha256=wrong",
        },
    )
    assert resp.status_code == 401


def test_non_meeting_started_event_is_ignored(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    resp = _post_webhook({"event_type": "meeting.completed", "data": {"meeting": {}}})
    assert resp.status_code == 200
    assert resp.json()["resolved"] is False


@respx.mock
def test_meeting_started_no_attendee_domain_match_reported_not_fatal(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_API_KEY", "vxa_rep_key")
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    resp = _post_webhook(_meeting_started_payload())
    assert resp.status_code == 200
    assert resp.json()["resolved"] is False


@respx.mock
def test_own_domain_is_excluded(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_API_KEY", "vxa_rep_key")
    monkeypatch.setenv("SALES_CYCLE_OWN_DOMAINS", "acme.example.com")
    # The only attendee is on our own domain — nothing left to search for.
    resp = _post_webhook(_meeting_started_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is False
    assert "no external attendee domain" in body["reason"]
