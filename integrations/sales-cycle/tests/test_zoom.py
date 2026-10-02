"""Connect Zoom — the per-rep OAuth flow, the signed webhook, and sending the bot to a meeting the rep just started."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

import sales_cycle.api as api_module
import sales_cycle.zoom_join as zoom_join
from conftest import GATEWAY, bare, client

SECRET = "internal-secret"
HOOK_SECRET = "zoom-hook-secret"
INTERNAL = {"X-Internal-Secret": SECRET}
ZOOM_TOKEN = "https://zoom.us/oauth/token"
ZOOM_ME = "https://api.zoom.us/v2/users/me"


@pytest.fixture(autouse=True)
def _zoom_env(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_INTERNAL_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_ZOOM_OAUTH_CLIENT_ID", "zid")
    monkeypatch.setenv("SALES_CYCLE_ZOOM_OAUTH_CLIENT_SECRET", "zsecret")
    monkeypatch.setenv("SALES_CYCLE_ZOOM_OAUTH_REDIRECT_URI", "https://sc.example.com/oauth/zoom/callback")
    monkeypatch.setenv("SALES_CYCLE_ZOOM_WEBHOOK_SECRET_TOKEN", HOOK_SECRET)
    monkeypatch.setenv("SALES_CYCLE_TERMINAL_URL", "https://terminal.example.com")
    monkeypatch.setattr(zoom_join, "MEETING_LOOKUP_DELAY_SEC", 0.0)


def _sign(body: bytes, *, secret=HOOK_SECRET, ts=None) -> dict:
    ts = str(int(time.time()) if ts is None else ts)
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return {"x-zm-request-timestamp": ts, "x-zm-signature": sig, "Content-Type": "application/json"}


def _connect(user="42", zoom_user="zu1", vexa_token="vx_bot_key") -> str:
    """Drive the whole connect flow through the routes, with Zoom mocked; returns the vexa user id."""
    link = client.post("/zoom/authorize-link", headers=INTERNAL,
                       json={"vexa_user_id": user, "vexa_token": vexa_token, "vexa_token_id": 7})
    assert link.status_code == 200
    state = parse_qs(urlparse(link.json()["url"]).query)["state"][0]
    with respx.mock:
        respx.post(ZOOM_TOKEN).mock(return_value=httpx.Response(200, json={
            "access_token": "acc1", "refresh_token": "ref1", "expires_in": 3600}))
        respx.get(ZOOM_ME).mock(return_value=httpx.Response(200, json={"id": zoom_user, "email": "rep@acme.com"}))
        cb = client.get("/oauth/zoom/callback", params={"code": "c", "state": state}, follow_redirects=False)
    assert cb.status_code in (302, 307) and "zoom_connected=1" in cb.headers["location"]
    return user


def _started(host="zu1", uuid="uuid-1", meeting_id="81234567890", topic="Acme demo") -> bytes:
    return json.dumps({"event": "meeting.started", "event_ts": 1, "payload": {
        "account_id": "a", "object": {"uuid": uuid, "id": meeting_id, "host_id": host, "topic": topic, "type": 2}}}).encode()


# ── the per-rep routes are the Terminal's alone ─────────────────────────────────────────────────────────

def test_per_rep_routes_refuse_a_caller_without_the_internal_secret(monkeypatch):
    for call in (lambda h: bare.post("/zoom/authorize-link", headers=h, json={"vexa_user_id": "1", "vexa_token": "t"}),
                 lambda h: bare.get("/zoom/status", headers=h, params={"vexa_user_id": "1"}),
                 lambda h: bare.post("/zoom/disconnect", headers=h, json={"vexa_user_id": "1"})):
        assert call({}).status_code == 401
        assert call({"X-Internal-Secret": "wrong"}).status_code == 401
    monkeypatch.setenv("SALES_CYCLE_INTERNAL_SECRET", "")
    assert bare.get("/zoom/status", headers={"X-Internal-Secret": ""}, params={"vexa_user_id": "1"}).status_code == 503


def test_authorize_link_503_when_zoom_oauth_is_not_configured(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_ZOOM_OAUTH_CLIENT_ID", "")
    r = client.post("/zoom/authorize-link", headers=INTERNAL, json={"vexa_user_id": "1", "vexa_token": "t"})
    assert r.status_code == 503
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "1"}).json()["configured"] is False


# ── connect / status / disconnect ───────────────────────────────────────────────────────────────────────

def test_connecting_saves_the_zoom_account_against_the_vexa_user():
    link = client.post("/zoom/authorize-link", headers=INTERNAL, json={"vexa_user_id": "42", "vexa_token": "k"}).json()["url"]
    parsed = urlparse(link)
    q = parse_qs(parsed.query)
    assert parsed.netloc == "zoom.us" and parsed.path == "/oauth/authorize"
    assert q["client_id"] == ["zid"] and q["redirect_uri"] == ["https://sc.example.com/oauth/zoom/callback"]

    _connect()
    status = client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()
    assert status == {"configured": True, "webhook_ready": True, "connected": True, "account_label": "rep@acme.com", "last_join": None}
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "43"}).json()["connected"] is False


def test_the_callback_state_is_single_use_and_unknown_states_are_refused():
    link = client.post("/zoom/authorize-link", headers=INTERNAL, json={"vexa_user_id": "42", "vexa_token": "k"}).json()["url"]
    state = parse_qs(urlparse(link).query)["state"][0]
    with respx.mock:
        respx.post(ZOOM_TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600}))
        respx.get(ZOOM_ME).mock(return_value=httpx.Response(200, json={"id": "zu1", "email": "e"}))
        first = client.get("/oauth/zoom/callback", params={"code": "c", "state": state}, follow_redirects=False)
        replay = client.get("/oauth/zoom/callback", params={"code": "c", "state": state}, follow_redirects=False)
        forged = client.get("/oauth/zoom/callback", params={"code": "c", "state": "nope"}, follow_redirects=False)
    assert "zoom_connected=1" in first.headers["location"]
    assert "zoom_error=expired" in replay.headers["location"] and "zoom_error=expired" in forged.headers["location"]


def test_a_declined_consent_or_failed_exchange_connects_nothing():
    link = client.post("/zoom/authorize-link", headers=INTERNAL, json={"vexa_user_id": "42", "vexa_token": "k"}).json()["url"]
    state = parse_qs(urlparse(link).query)["state"][0]
    denied = client.get("/oauth/zoom/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert "zoom_error=access_denied" in denied.headers["location"]

    link = client.post("/zoom/authorize-link", headers=INTERNAL, json={"vexa_user_id": "42", "vexa_token": "k"}).json()["url"]
    state = parse_qs(urlparse(link).query)["state"][0]
    with respx.mock:
        respx.post(ZOOM_TOKEN).mock(return_value=httpx.Response(400, json={"reason": "bad code"}))
        failed = client.get("/oauth/zoom/callback", params={"code": "x", "state": state}, follow_redirects=False)
    assert "zoom_error=exchange_failed" in failed.headers["location"]
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()["connected"] is False


def test_disconnect_forgets_the_account_and_returns_the_vexa_key_to_revoke():
    _connect()
    with respx.mock:
        revoke = respx.post("https://zoom.us/oauth/revoke").mock(return_value=httpx.Response(200, json={"status": "success"}))
        r = client.post("/zoom/disconnect", headers=INTERNAL, json={"vexa_user_id": "42"})
    assert r.json() == {"connected": False, "vexa_token_id": "7"} and revoke.called
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()["connected"] is False
    assert client.post("/zoom/disconnect", headers=INTERNAL, json={"vexa_user_id": "42"}).json() == {"connected": False, "vexa_token_id": None}


def test_a_second_vexa_user_connecting_the_same_zoom_account_takes_it_over():
    _connect(user="42", zoom_user="zuX")
    _connect(user="43", zoom_user="zuX")
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()["connected"] is False
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "43"}).json()["connected"] is True


# ── the webhook ─────────────────────────────────────────────────────────────────────────────────────────

def test_the_endpoint_validation_handshake_returns_the_signed_token():
    r = client.post("/webhooks/zoom", json={"event": "endpoint.url_validation", "payload": {"plainToken": "abcDEF123456"}})
    assert r.status_code == 200
    assert r.json() == {"plainToken": "abcDEF123456",
                        "encryptedToken": hmac.new(HOOK_SECRET.encode(), b"abcDEF123456", hashlib.sha256).hexdigest()}


def test_the_handshake_will_not_sign_text_shaped_like_a_notification():
    """It signs whatever it is handed with the key that signs notifications, so a token carrying the signed
    string's own punctuation would be a forged-notification oracle."""
    forged = f"v0:{int(time.time())}:" + '{"event":"meeting.started"}'
    r = client.post("/webhooks/zoom", json={"event": "endpoint.url_validation", "payload": {"plainToken": forged}})
    assert r.status_code == 400


def test_a_notification_must_be_signed_by_zoom():
    body = _started()
    assert client.post("/webhooks/zoom", content=body).status_code == 401
    assert client.post("/webhooks/zoom", content=body, headers=_sign(body, secret="wrong")).status_code == 401
    assert client.post("/webhooks/zoom", content=body, headers=_sign(body, ts=int(time.time()) - 3600)).status_code == 401


def test_a_notification_for_a_host_nobody_connected_is_ignored():
    body = _started(host="stranger")
    with respx.mock:
        r = client.post("/webhooks/zoom", content=body, headers=_sign(body))
    assert r.status_code == 200 and r.json()["ignored"] == "no connected host"


# ── sending the bot ─────────────────────────────────────────────────────────────────────────────────────

def _meeting_api(join_url="https://acme.zoom.us/j/81234567890?pwd=abc"):
    return respx.get("https://api.zoom.us/v2/meetings/81234567890").mock(
        return_value=httpx.Response(200, json={"join_url": join_url, "topic": "Acme demo"}))


def test_a_started_meeting_sends_the_bot_as_the_rep_with_the_meetings_own_join_link():
    _connect()
    body = _started()
    with respx.mock:
        _meeting_api()
        bots = respx.post(f"{GATEWAY}/bots").mock(return_value=httpx.Response(201, json={"id": 9}))
        r = client.post("/webhooks/zoom", content=body, headers=_sign(body))
    assert r.status_code == 200
    sent = bots.calls.last.request
    assert sent.headers["x-api-key"] == "vx_bot_key"
    assert json.loads(sent.content) == {"meeting_url": "https://acme.zoom.us/j/81234567890?pwd=abc"}
    last = client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()["last_join"]
    assert last["outcome"] == "joined" and last["topic"] == "Acme demo"


def test_a_redelivered_notification_does_not_send_a_second_bot():
    _connect()
    body = _started()
    with respx.mock:
        _meeting_api()
        bots = respx.post(f"{GATEWAY}/bots").mock(return_value=httpx.Response(201, json={}))
        client.post("/webhooks/zoom", content=body, headers=_sign(body))
        client.post("/webhooks/zoom", content=body, headers=_sign(body))
    assert bots.call_count == 1


def test_a_meeting_zoom_will_not_describe_still_gets_the_bot_on_the_bare_link():
    _connect()
    body = _started()
    with respx.mock:
        respx.get("https://api.zoom.us/v2/meetings/81234567890").mock(return_value=httpx.Response(404, json={}))
        bots = respx.post(f"{GATEWAY}/bots").mock(return_value=httpx.Response(201, json={}))
        client.post("/webhooks/zoom", content=body, headers=_sign(body))
    assert json.loads(bots.calls.last.request.content) == {"meeting_url": "https://zoom.us/j/81234567890"}


_CAP = {"detail": {"code": "service_not_allowed", "reason": "concurrency_limit_reached"}}


@pytest.mark.parametrize("status,payload,outcome", [
    (409, {"detail": "x"}, "already_joined"),
    (401, {"detail": "x"}, "vexa_key_rejected"),
    (429, {"detail": "x"}, "limit_reached"),
    (403, _CAP, "limit_reached"),          # Vexa's documented-but-wrong 403 for the concurrency limit
    (403, {"detail": "Insufficient scope"}, "failed"),     # any other 403 is NOT a rejected key or a quota
    (500, {"detail": "x"}, "failed"),
])
def test_each_way_vexa_can_answer_is_recorded_not_swallowed(status, payload, outcome):
    _connect()
    body = _started()
    with respx.mock:
        _meeting_api()
        respx.post(f"{GATEWAY}/bots").mock(return_value=httpx.Response(status, json=payload))
        client.post("/webhooks/zoom", content=body, headers=_sign(body))
    last = client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()["last_join"]
    assert last["outcome"] == outcome
    assert (last["detail"] is not None) == (outcome in ("vexa_key_rejected", "failed", "limit_reached"))


def test_an_expired_zoom_token_is_refreshed_and_the_rotated_refresh_token_kept():
    _connect()
    store = api_module.get_store()
    conn = store.get_zoom_connection("42")
    store.update_zoom_tokens("42", access_token="stale", refresh_token="ref1", expires_at=time.time() - 10)
    body = _started()
    with respx.mock:
        refresh = respx.post(ZOOM_TOKEN).mock(return_value=httpx.Response(200, json={
            "access_token": "acc2", "refresh_token": "ref2", "expires_in": 3600}))
        meeting = _meeting_api()
        respx.post(f"{GATEWAY}/bots").mock(return_value=httpx.Response(201, json={}))
        client.post("/webhooks/zoom", content=body, headers=_sign(body))
    assert b"grant_type=refresh_token" in refresh.calls.last.request.content
    assert meeting.calls.last.request.headers["authorization"] == "Bearer acc2"
    after = store.get_zoom_connection("42")
    assert after.refresh_token == "ref2" and after.access_token == "acc2" and conn.vexa_token == after.vexa_token


def test_the_rep_removing_the_app_on_zoom_drops_the_connection():
    _connect()
    body = json.dumps({"event": "app_deauthorized", "payload": {"user_id": "zu1", "account_id": "a"}}).encode()
    assert client.post("/webhooks/zoom", content=body, headers=_sign(body)).status_code == 200
    assert client.get("/zoom/status", headers=INTERNAL, params={"vexa_user_id": "42"}).json()["connected"] is False
