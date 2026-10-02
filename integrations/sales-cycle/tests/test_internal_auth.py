"""The service is published, so which routes the internet can reach is a decision, not an accident: signed webhooks, OAuth redirects,
the key-authenticated helpers and /health are public; everything else needs the shared secret."""
import pytest

from conftest import INTERNAL_SECRET, bare, client
from sales_cycle import internal_auth

PRIVATE = [
    ("POST", "/internal/process-approved"), ("POST", "/internal/sweep-live-watchers"),
    ("GET", "/oauth/hubspot/status"), ("POST", "/oauth/slack/disconnect"), ("POST", "/oauth/hubspot/token"),
    ("GET", "/slack/channel"), ("POST", "/slack/channel"), ("GET", "/slack/channel-status"),
    ("GET", "/zoom/status?vexa_user_id=1"), ("POST", "/zoom/disconnect"),
    ("GET", "/anything/added/later"),                       # an unknown path is private, not public
]


@pytest.mark.parametrize("method,path", PRIVATE)
def test_a_private_route_refuses_a_caller_without_the_secret(method, path):
    assert bare.request(method, path, json={} if method == "POST" else None).status_code == 401
    wrong = bare.request(method, path, headers={"X-Internal-Secret": "nope"}, json={} if method == "POST" else None)
    assert wrong.status_code == 401


@pytest.mark.parametrize("method,path", [m for m in PRIVATE if "later" not in m[1]])
def test_a_private_route_answers_a_caller_with_it(method, path):
    r = client.request(method, path, json={} if method == "POST" else None)
    assert r.status_code not in (401, 503), (path, r.status_code, r.text)


def test_the_disconnect_of_a_connection_is_not_open_to_the_internet(tmp_path):
    """The reason this exists: /oauth/<provider>/disconnect used to answer anyone who could reach the service."""
    assert bare.post("/oauth/slack/disconnect").status_code == 401


def test_the_public_routes_stay_public():
    assert bare.get("/health").status_code == 200
    assert bare.get("/oauth/hubspot/authorize", follow_redirects=False).status_code in (307, 503)   # a redirect, or "not configured"
    assert bare.get("/oauth/zoom/callback", params={"error": "x"}, follow_redirects=False).status_code in (302, 307)
    assert bare.post("/slack/events", content=b"{}").status_code == 401         # public, but it wants Slack's signature
    assert bare.post("/webhooks/zoom", content=b"{}").status_code == 401        # public, but it wants Zoom's signature
    assert bare.post("/webhooks/meeting-started", content=b"{}").status_code != 503
    assert bare.post("/tag", json={}).status_code == 422                        # reaches the handler: it wants a Vexa API key


def test_with_no_secret_configured_the_private_routes_are_closed(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_INTERNAL_SECRET", "")
    r = client.get("/slack/channel")
    assert r.status_code == 503
    assert bare.get("/health").status_code == 200                                # the public ones do not depend on it


def test_the_public_list_is_exactly_what_it_says():
    pub = ["/health", "/oauth/hubspot/authorize", "/oauth/slack/callback", "/slack/events", "/webhooks/meeting-started", "/webhooks/zoom", "/tag", "/dispatch"]
    assert all(internal_auth.is_public(p) for p in pub)
    priv = ["/oauth/hubspot/status", "/oauth/slack/disconnect", "/oauth/hubspot/token", "/slack/channel", "/internal/process-approved", "/zoom/status", "/health/x", "/webhooks", "/oauth/hubspot/authorize/x"]
    assert not any(internal_auth.is_public(p) for p in priv)
    assert INTERNAL_SECRET                                                       # the fixture's secret exists
