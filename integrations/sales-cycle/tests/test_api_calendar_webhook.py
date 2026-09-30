import hashlib
import hmac
import json
import time

import httpx
import respx

import sales_cycle.api as api_module
from conftest import AGENT_API, GATEWAY, client

SECRET = "test-webhook-secret"


def _mock_process_call() -> None:
    """Every meeting.started payload in this file carries id/platform/native_meeting_id, so
    webhook_meeting_started's new auto-processing background task (enable_copilot_processing)
    always fires a POST here — under @respx.mock that call must be mocked like any other, or
    respx's strict all-mocked check fails the test even though the assertion under test is
    unrelated to this call."""
    respx.post(f"{AGENT_API}/api/meeting/process").mock(return_value=httpx.Response(200, json={"processing": True}))


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

    _mock_process_call()
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


@respx.mock
def test_meeting_started_launches_the_live_card_watcher_as_the_meeting_owner(monkeypatch):
    """The live watcher must run as the meeting's OWNER (`user_id`), never the resolved customer
    workspace — that's what agent-api's SSE ownership check and meeting-api's own records are keyed
    on. Stubs out `watch_meeting` itself (an async, real-network call) — this test is about the
    wiring, not the watcher's own behavior (covered by test_live_card_watcher.py)."""
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_API_KEY", "vxa_rep_key")
    _mock_process_call()
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    calls = []

    async def _fake_watch_meeting(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(api_module, "watch_meeting", _fake_watch_meeting)

    resp = _post_webhook(_meeting_started_payload(id=99, user_id=7))
    assert resp.status_code == 200
    assert len(calls) == 1
    assert calls[0]["meeting_id"] == "99"
    assert calls[0]["subject"] == "7"
    # The durable half of the same call — survives a restart, unlike the in-process task itself
    # (see sweep_live_watchers, whose whole job is noticing when this row outlives its task).
    assert api_module.get_store().list_active_watchers() == [("99", "7")]


def test_meeting_started_missing_owner_id_does_not_start_a_watcher(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    calls = []
    monkeypatch.setattr(api_module, "watch_meeting", lambda **kw: calls.append(kw))
    # Not what this test is about, but the payload still carries id/platform/native_meeting_id, so
    # the auto-processing background task still fires — stub it out the same way watch_meeting is,
    # rather than a real (failing, DNS-less-test-env) network call.
    monkeypatch.setattr(api_module, "enable_copilot_processing", lambda **kw: None)
    resp = _post_webhook(_meeting_started_payload())  # no user_id override — payload lacks it
    assert resp.status_code == 200
    assert calls == []


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
    _mock_process_call()
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
    _mock_process_call()
    # The only attendee is on our own domain — nothing left to search for.
    resp = _post_webhook(_meeting_started_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved"] is False
    assert "no external attendee domain" in body["reason"]
