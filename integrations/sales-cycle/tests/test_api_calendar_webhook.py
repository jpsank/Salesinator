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


@respx.mock
def test_meeting_started_skips_starting_a_watcher_already_tracked_as_live(monkeypatch):
    """Reproduced live: Vexa delivered meeting.started TWICE for the same real meeting, and
    _start_watcher had no guard against a second concurrent SSE reader on the same copilot feed —
    unlike sweep_live_watchers' own call site, which already checks _watch_meeting_tasks first. The
    result: a real feature_request card was tagged correctly but never reached Slack — neither of the
    two racing watchers ever posted anything. A delivery for a meeting already tracked as live must be
    a no-op here, the same as it already is for the sweep.

    Exercises the guard directly against _watch_meeting_tasks rather than through two real HTTP calls:
    TestClient tears down its event loop between separate .post() calls, so a task's own asyncio-level
    "still running" state doesn't survive to prove anything across them — the dict is the real, durable
    signal both call sites (this handler and the sweep) actually check."""
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_API_KEY", "vxa_rep_key")
    _mock_process_call()
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    class _StillRunningTask:
        def done(self) -> bool:
            return False

    api_module._watch_meeting_tasks["55"] = _StillRunningTask()
    try:
        calls = []
        monkeypatch.setattr(api_module, "watch_meeting", lambda **kw: calls.append(kw))

        resp = _post_webhook(_meeting_started_payload(id=55, user_id=9))

        assert resp.status_code == 200
        assert calls == []  # already tracked as live — no second watcher started
    finally:
        api_module._watch_meeting_tasks.pop("55", None)  # shared module state — clean up


@respx.mock
def test_meeting_started_restarts_a_watcher_whose_task_already_finished(monkeypatch):
    """The mirror case: a stale, DONE entry (the meeting's earlier watcher already ended) must NOT
    block a fresh one from starting — only a genuinely still-running task should suppress it."""
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_TOKEN", "test-token")
    monkeypatch.setenv("SALES_CYCLE_CALENDAR_API_KEY", "vxa_rep_key")
    _mock_process_call()
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )

    class _FinishedTask:
        def done(self) -> bool:
            return True

    api_module._watch_meeting_tasks["56"] = _FinishedTask()
    try:
        calls = []

        async def _fake_watch_meeting(**kwargs):
            calls.append(kwargs)

        monkeypatch.setattr(api_module, "watch_meeting", _fake_watch_meeting)

        resp = _post_webhook(_meeting_started_payload(id=56, user_id=9))

        assert resp.status_code == 200
        assert len(calls) == 1
    finally:
        api_module._watch_meeting_tasks.pop("56", None)


def test_watcher_task_finished_does_not_delete_a_newer_tasks_entry():
    """Found by code review, not live yet: a task's done-callback fires asynchronously, so an OLDER
    task's (now-finished) callback can run AFTER a crash-restart has already registered a NEWER task
    for the same meeting_id. Popping the dict entry unconditionally by key would delete the new
    task's live entry out from under it — reintroducing "two watchers, neither posts" through the
    cleanup path instead of the start path this session's earlier fix closed. The stale callback
    must check it's still the tracked task before popping anything."""
    class _Task:
        pass

    task_a, task_b = _Task(), _Task()
    api_module._watch_meeting_tasks["77"] = task_b  # a newer task already replaced task_a's entry

    api_module._watcher_task_finished("77", task_a)  # task_a's own stale callback fires

    assert api_module._watch_meeting_tasks.get("77") is task_b
    api_module._watch_meeting_tasks.pop("77", None)


def test_watcher_task_finished_pops_when_it_is_still_the_tracked_task():
    class _Task:
        pass

    task_a = _Task()
    api_module._watch_meeting_tasks["78"] = task_a

    api_module._watcher_task_finished("78", task_a)

    assert "78" not in api_module._watch_meeting_tasks


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
