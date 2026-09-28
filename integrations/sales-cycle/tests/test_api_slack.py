import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx
import respx

import sales_cycle.api as api_module
from conftest import client

SECRET = "test-signing-secret"


def _sign(timestamp: str, body: bytes) -> str:
    basestring = f"v0:{timestamp}:{body.decode()}".encode()
    return "v0=" + hmac.new(SECRET.encode(), basestring, hashlib.sha256).hexdigest()


def _post_event(payload: dict):
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    return client.post(
        "/slack/events", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": _sign(ts, body),
        },
    )


def test_url_verification_handshake(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    resp = _post_event({"type": "url_verification", "challenge": "abc123"})
    assert resp.status_code == 200
    assert resp.json() == {"challenge": "abc123"}


def test_bad_signature_is_rejected(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    resp = client.post(
        "/slack/events", content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=wrong",
        },
    )
    assert resp.status_code == 401


def test_reaction_added_approves_pending_message(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    api_module._store = None  # reset the lazy singleton so the new db_path takes effect
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))

    store = api_module.get_store()
    store.record_pending_approval(
        slack_channel="C1", slack_ts="100.001", workspace_id="cust-1",
        entity_path="x.md", title="CSV export", body="wants it",
    )

    resp = _post_event({
        "type": "event_callback",
        "event": {"type": "reaction_added", "reaction": "white_check_mark",
                  "item": {"channel": "C1", "ts": "100.001"}},
    })
    assert resp.status_code == 200
    pending = store.list_approved_unprocessed()
    assert len(pending) == 1
    assert pending[0].title == "CSV export"


def test_reaction_added_ignores_unrelated_emoji(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))
    store = api_module.get_store()
    store.record_pending_approval(
        slack_channel="C1", slack_ts="100.001", workspace_id="cust-1",
        entity_path="x.md", title="CSV export", body="wants it",
    )

    resp = _post_event({
        "type": "event_callback",
        "event": {"type": "reaction_added", "reaction": "thumbsup",
                  "item": {"channel": "C1", "ts": "100.001"}},
    })
    assert resp.status_code == 200
    assert store.list_approved_unprocessed() == []


@respx.mock
def test_poll_feature_requests_endpoint(monkeypatch, tmp_path: Path):
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))
    monkeypatch.setenv("SALES_CYCLE_WORKSPACES_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")

    d = tmp_path / "workspaces" / "cust-1" / "kg" / "entities" / "feature_request"
    d.mkdir(parents=True)
    (d / "csv-export.md").write_text("---\ntype: feature_request\nid: csv-export\ntitle: CSV export\n---\nwants it\n")

    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )

    resp = client.post("/internal/poll-feature-requests")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
