"""POST /bots with ``capture: "external"`` — a meeting whose audio a client streams to the capture host.

The row is created through the SAME gates as a bot (STT, dedupe, cap) and nothing is spawned; the lifecycle
(``joining`` → ``active``) is reported in-process through the entry a bot's own callback uses, so the FSM,
persistence and the ``meeting.started`` webhook behave exactly as for a bot. Driven over the unified ``create_app``
with the in-memory fakes, offline.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from meeting_api import create_app
from meeting_api.bot_spawn.fakes import FakeRuntimeClient, InMemoryMeetingRepo

HEADERS = {"x-user-id": "7"}


@pytest.fixture(autouse=True)
def _stt(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_URL", "http://stt.test")
    monkeypatch.setenv("TRANSCRIPTION_SERVICE_TOKEN", "t")


def _client():
    repo, runtime = InMemoryMeetingRepo(), FakeRuntimeClient()
    return TestClient(create_app(meeting_repo=repo, runtime=runtime)), repo, runtime


def test_an_external_capture_creates_an_active_meeting_and_spawns_nothing():
    client, repo, runtime = _client()
    r = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "native_meeting_id": "cap-abc123", "capture": "external"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "active"
    assert body["platform"] == "zoom" and body["native_meeting_id"] == "cap-abc123"
    assert body["bot_container_id"] is None
    assert body["data"]["capture_source"] == "external"
    assert len(body["data"]["sessions"]) == 1
    assert runtime.specs == []                                                 # no workload was requested


def test_it_needs_no_meeting_url_unlike_a_zoom_bot():
    client, _, _ = _client()
    bot = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "native_meeting_id": "81234567890"})
    assert bot.status_code == 422                                              # a zoom BOT must be told where to go
    capture = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "native_meeting_id": "81234567890", "capture": "external"})
    assert capture.status_code == 201


def test_it_is_deduped_like_a_bot():
    client, _, _ = _client()
    body = {"platform": "zoom", "native_meeting_id": "cap-1", "capture": "external"}
    assert client.post("/bots", headers=HEADERS, json=body).status_code == 201
    assert client.post("/bots", headers=HEADERS, json=body).status_code == 409


def test_it_is_refused_without_a_transcription_backend(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_SERVICE_URL")
    client, _, _ = _client()
    r = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "native_meeting_id": "cap-1", "capture": "external"})
    assert r.status_code == 503


def test_any_other_capture_value_is_a_422():
    client, _, _ = _client()
    r = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "native_meeting_id": "cap-1", "capture": "bot"})
    assert r.status_code == 422


def test_the_session_it_made_accepts_the_terminal_the_capture_host_reports():
    client, _, _ = _client()
    body = client.post("/bots", headers=HEADERS, json={"platform": "zoom", "native_meeting_id": "cap-1", "capture": "external"}).json()
    session = body["data"]["sessions"][-1]
    done = client.post("/bots/internal/callback/lifecycle", json={"connection_id": session, "status": "completed", "completion_reason": "stopped"})
    assert done.status_code == 200, done.text
    after = client.get("/bots/status", headers=HEADERS)
    assert after.status_code == 200
