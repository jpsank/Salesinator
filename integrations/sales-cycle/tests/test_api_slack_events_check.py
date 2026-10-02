"""Is Slack actually delivering events? The status line, and the check that proves it end to end: the bot reacts 👀 to the latest card,
waits for Slack to report the reaction back, and takes it off — saying which way it failed when it did (nothing arrived, or it arrived and
was refused). A quiet channel and a broken connection look alike from the outside; this tells them apart."""
import hashlib
import hmac
import json
import time

import httpx
import pytest
import respx

import sales_cycle.api as api_module
from conftest import bare, client

SECRET = "test-signing-secret"
SLACK = "https://slack.com/api"
CH, TS = "C1", "100.001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "C1")
    monkeypatch.setattr(api_module, "_EVENTS_CHECK_WAIT_S", 0.6)
    api_module._slack_deliveries.clear()


def _card(store):
    store.record_pending_approval(slack_channel=CH, slack_ts=TS, workspace_id="w", source_key="k", title="T", body="B")


def _signed_event(reaction="eyes", ts=TS):
    body = json.dumps({"type": "event_callback", "event": {"type": "reaction_added", "reaction": reaction, "item": {"channel": CH, "ts": ts}}}).encode()
    now = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{now}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return {"content": body, "headers": {"Content-Type": "application/json", "X-Slack-Request-Timestamp": now, "X-Slack-Signature": sig}}


def _slack(*, on_add=None, add_error=None, posted_ts="300.1"):
    """Mocks the Slack calls the check makes; ``on_add`` runs when the bot adds its reaction (it stands in for Slack delivering the event)."""
    def add(request):
        if add_error:
            return httpx.Response(200, json={"ok": False, "error": add_error})
        if on_add:
            on_add(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})
    return {
        "add": respx.post(f"{SLACK}/reactions.add").mock(side_effect=add),
        "remove": respx.post(f"{SLACK}/reactions.remove").mock(return_value=httpx.Response(200, json={"ok": True})),
        "post": respx.post(f"{SLACK}/chat.postMessage").mock(return_value=httpx.Response(200, json={"ok": True, "ts": posted_ts})),
        "delete": respx.post(f"{SLACK}/chat.delete").mock(return_value=httpx.Response(200, json={"ok": True})),
    }


def _delivers(ok=True, reaction="eyes", ts=None):
    return lambda payload: api_module._note_slack_delivery(ok=ok, event={"type": "reaction_added", "reaction": reaction, "item": {"ts": ts or payload["timestamp"]}})


# ── the status line ──

def test_nothing_received_yet_is_reported_as_nothing():
    assert client.get("/slack/events-status").json() == {"last_event_at": None, "last_rejected_at": None}


def test_a_delivered_event_updates_the_last_event_time():
    before = time.time()
    assert client.post("/slack/events", **_signed_event()).status_code == 200
    got = client.get("/slack/events-status").json()
    assert got["last_event_at"] >= before and got["last_rejected_at"] is None


def test_a_request_from_slack_with_a_bad_signature_is_recorded_as_refused():
    bad = _signed_event(); bad["headers"]["X-Slack-Signature"] = "v0=" + "0" * 64
    assert client.post("/slack/events", content=bad["content"], headers={**bad["headers"], "User-Agent": "Slackbot 1.0 (+https://api.slack.com/robots)"}).status_code == 401
    got = client.get("/slack/events-status").json()
    assert got["last_rejected_at"] is not None and got["last_event_at"] is None


def test_a_strangers_unsigned_post_is_not_recorded_so_it_cannot_fake_a_warning():
    assert client.post("/slack/events", content=b"{}", headers={"Content-Type": "application/json"}).status_code == 401
    assert client.get("/slack/events-status").json()["last_rejected_at"] is None


# ── the check ──

@respx.mock
def test_events_arriving_is_reported_and_the_reaction_is_taken_off_again():
    _card(api_module.get_store())
    m = _slack(on_add=_delivers())
    r = client.post("/slack/events-check").json()
    assert r["delivered"] is True and r["target"] == "your latest feature-request card" and "arriving" in r["detail"]
    assert json.loads(m["add"].calls[0].request.content) == {"channel": CH, "timestamp": TS, "name": "eyes"}
    assert m["remove"].call_count == 1 and not m["post"].called and not m["delete"].called       # nothing left behind, no message posted


@respx.mock
def test_when_nothing_arrives_it_says_slack_is_not_sending_and_what_to_check():
    _card(api_module.get_store())
    m = _slack()
    r = client.post("/slack/events-check").json()
    assert r["delivered"] is False and r["refused"] is False
    assert "Socket Mode is OFF" in r["detail"] and "Event Subscriptions" in r["detail"]
    assert m["remove"].call_count == 1


@respx.mock
def test_when_slack_sends_but_the_signature_fails_it_points_at_the_signing_secret():
    _card(api_module.get_store())
    _slack(on_add=_delivers(ok=False))
    r = client.post("/slack/events-check").json()
    assert r["delivered"] is False and r["refused"] is True and "Signing Secret" in r["detail"]


@respx.mock
def test_an_event_for_some_other_message_or_reaction_does_not_count():
    _card(api_module.get_store())
    _slack(on_add=lambda p: (_delivers(ts="999.9")(p), _delivers(reaction="+1")(p)))
    assert client.post("/slack/events-check").json()["delivered"] is False


@respx.mock
def test_with_no_card_yet_it_uses_a_short_message_and_deletes_it():
    m = _slack(on_add=_delivers())
    r = client.post("/slack/events-check").json()
    assert r["delivered"] is True and "posts and deletes" in r["target"]
    assert json.loads(m["post"].calls[0].request.content)["channel"] == "C1"
    assert json.loads(m["delete"].calls[0].request.content) == {"channel": "C1", "ts": "300.1"}
    assert json.loads(m["remove"].calls[0].request.content)["timestamp"] == "300.1"


@respx.mock
def test_with_no_card_and_no_channel_it_says_what_is_missing(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_CHANNEL_ID", "")
    m = _slack()
    r = client.post("/slack/events-check").json()
    assert r["delivered"] is False and "channel" in r["detail"] and not m["add"].called


@respx.mock
def test_a_missing_reactions_write_scope_is_explained_not_a_crash():
    _card(api_module.get_store())
    _slack(add_error="missing_scope")
    r = client.post("/slack/events-check").json()
    assert r["delivered"] is False and "reactions:write" in r["detail"]


# ── private ──

def test_both_routes_are_private():
    assert bare.get("/slack/events-status").status_code == 401
    assert bare.post("/slack/events-check").status_code == 401
