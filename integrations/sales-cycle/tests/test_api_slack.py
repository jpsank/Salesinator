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


@respx.mock
def test_reaction_added_approves_and_dispatches_in_real_time(monkeypatch, tmp_path: Path):
    """The ✅ both approves AND starts the implementation turn — no waiting for the next cron
    sweep. TestClient runs BackgroundTasks synchronously before returning, so by the time this
    call returns, `_dispatch_one` has already run and moved the row to 'dispatched'."""
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_AGENT_API_INTERNAL_URL", "http://agent-api:8100")
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_SUBJECT", "product-repo")
    api_module._store = None  # reset the lazy singleton so the new db_path takes effect
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))

    store = api_module.get_store()
    store.record_pending_approval(
        slack_channel="C1", slack_ts="100.001", workspace_id="cust-1",
        source_key="x.md", title="CSV export", body="wants it",
    )
    respx.post("http://agent-api:8100/invocations").mock(
        return_value=httpx.Response(202, json={"workload_id": "agent-1"})
    )

    resp = _post_event({
        "type": "event_callback",
        "event": {"type": "reaction_added", "reaction": "white_check_mark",
                  "item": {"channel": "C1", "ts": "100.001"}},
    })
    assert resp.status_code == 200
    assert store.list_approved_unprocessed() == []  # already claimed + dispatched, not left pending
    dispatched = store.list_dispatched_unpushed()
    assert len(dispatched) == 1
    assert dispatched[0].title == "CSV export"
    assert dispatched[0].branch == "feature/csv-export"


@respx.mock
def test_reaction_added_dispatch_failure_leaves_it_approved_for_the_cron_sweep(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_AGENT_API_INTERNAL_URL", "http://agent-api:8100")
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_SUBJECT", "product-repo")
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))

    store = api_module.get_store()
    store.record_pending_approval(
        slack_channel="C1", slack_ts="100.001", workspace_id="cust-1",
        source_key="x.md", title="CSV export", body="wants it",
    )
    respx.post("http://agent-api:8100/invocations").mock(return_value=httpx.Response(500))

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
        source_key="x.md", title="CSV export", body="wants it",
    )

    resp = _post_event({
        "type": "event_callback",
        "event": {"type": "reaction_added", "reaction": "thumbsup",
                  "item": {"channel": "C1", "ts": "100.001"}},
    })
    assert resp.status_code == 200
    assert store.list_approved_unprocessed() == []


# ---- GET /slack/channel-status — the Settings page's live "will this actually work?" check -------

def test_channel_status_unconfigured_when_no_channel_id_set(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "")
    resp = client.get("/slack/channel-status")
    assert resp.status_code == 200
    assert resp.json() == {
        "configured": False, "channel_id": None, "channel_name": None,
        "is_member": None, "error": None,
    }


@respx.mock
def test_channel_status_reports_a_real_member_channel(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C0C4ZQV4YJY")
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    respx.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(200, json={
            "ok": True, "channel": {"id": "C0C4ZQV4YJY", "name": "feature-requests", "is_member": True},
        })
    )
    resp = client.get("/slack/channel-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["channel_name"] == "feature-requests"
    assert body["is_member"] is True
    assert body["error"] is None


@respx.mock
def test_channel_status_reports_is_member_false_for_a_public_channel_not_joined(monkeypatch):
    """A public channel the app can SEE but hasn't been invited into — the exact live bug this whole
    feature exists to catch, reproduced here as a fixed regression test."""
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C0C4ZQV4YJY")
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    respx.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(200, json={
            "ok": True, "channel": {"id": "C0C4ZQV4YJY", "name": "feature-requests", "is_member": False},
        })
    )
    resp = client.get("/slack/channel-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_member"] is False
    assert body["error"] is None


@respx.mock
def test_channel_status_reports_channel_not_found(monkeypatch):
    """A bad channel ID, or a private channel the app was never invited into (Slack hides private
    channels from non-members entirely — same error shape either way)."""
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C_DOES_NOT_EXIST")
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    respx.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )
    resp = client.get("/slack/channel-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["is_member"] is None
    assert body["error"] == "channel_not_found"


# ---- GET/POST /slack/channel — the Settings page's own "which channel" control ------------------

def test_get_slack_channel_reports_env_default_when_no_override_set(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C_ENV_DEFAULT")
    resp = client.get("/slack/channel")
    assert resp.status_code == 200
    assert resp.json() == {"channel_id": "C_ENV_DEFAULT", "source": "env"}


def test_get_slack_channel_reports_unset_when_neither_override_nor_env_is_set(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "")
    resp = client.get("/slack/channel")
    assert resp.status_code == 200
    assert resp.json() == {"channel_id": None, "source": "unset"}


def test_post_slack_channel_sets_an_override_that_wins_over_the_env_default(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C_ENV_DEFAULT")
    resp = client.post("/slack/channel", json={"channel_id": "C_FROM_UI"})
    assert resp.status_code == 200
    assert resp.json() == {"channel_id": "C_FROM_UI", "source": "override"}
    # Reflected back on a plain GET too — not just the POST response.
    assert client.get("/slack/channel").json() == {"channel_id": "C_FROM_UI", "source": "override"}


def test_post_slack_channel_with_empty_string_clears_the_override(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C_ENV_DEFAULT")
    client.post("/slack/channel", json={"channel_id": "C_FROM_UI"})
    resp = client.post("/slack/channel", json={"channel_id": ""})
    assert resp.status_code == 200
    assert resp.json() == {"channel_id": "C_ENV_DEFAULT", "source": "env"}


@respx.mock
def test_channel_status_checks_the_override_not_the_env_default(monkeypatch):
    """The whole point of the override: it must actually be what gets checked (and, by the same
    `_slack_channel_id()` helper, what live cards post to) — not just what the GET endpoint echoes
    back while /slack/channel-status keeps reading the stale env value underneath it."""
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C_ENV_DEFAULT")
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    client.post("/slack/channel", json={"channel_id": "C_FROM_UI"})
    route = respx.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(200, json={
            "ok": True, "channel": {"id": "C_FROM_UI", "name": "ui-picked", "is_member": True},
        })
    )
    resp = client.get("/slack/channel-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["channel_id"] == "C_FROM_UI"
    assert body["channel_name"] == "ui-picked"
    assert route.calls[0].request.url.params["channel"] == "C_FROM_UI"
