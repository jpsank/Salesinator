import time

import httpx
import respx

from sales_cycle.hubspot_oauth import (
    HubSpotOAuthError, build_authorize_url, exchange_code, get_valid_access_token,
)
from sales_cycle.store import Store


def test_build_authorize_url_includes_client_id_and_redirect_and_scopes():
    url = build_authorize_url(
        client_id="abc123", redirect_uri="http://localhost:8200/oauth/hubspot/callback",
        scopes="crm.objects.companies.read",
    )
    assert url.startswith("https://app.hubspot.com/oauth/authorize?")
    assert "client_id=abc123" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8200" in url
    assert "scope=crm.objects.companies.read" in url


@respx.mock
def test_exchange_code_saves_connection_with_account_label():
    respx.post("https://api.hubapi.com/oauth/v1/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800,
        })
    )
    respx.get("https://api.hubapi.com/oauth/v1/access-tokens/at-1").mock(
        return_value=httpx.Response(200, json={"hub_domain": "acme.hubspot.com"})
    )
    store = Store(":memory:")
    connection = exchange_code(
        store=store, client_id="cid", client_secret="csecret",
        redirect_uri="http://localhost:8200/oauth/hubspot/callback", code="the-code",
    )
    assert connection.access_token == "at-1"
    assert connection.refresh_token == "rt-1"
    assert connection.account_label == "acme.hubspot.com"

    saved = store.get_oauth_connection("hubspot")
    assert saved is not None
    assert saved.access_token == "at-1"


@respx.mock
def test_exchange_code_account_label_lookup_failure_is_not_fatal():
    respx.post("https://api.hubapi.com/oauth/v1/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800,
        })
    )
    respx.get("https://api.hubapi.com/oauth/v1/access-tokens/at-1").mock(return_value=httpx.Response(500))
    store = Store(":memory:")
    connection = exchange_code(
        store=store, client_id="cid", client_secret="csecret",
        redirect_uri="http://localhost:8200/oauth/hubspot/callback", code="the-code",
    )
    assert connection.access_token == "at-1"
    assert connection.account_label is None


def test_get_valid_access_token_none_when_never_connected():
    store = Store(":memory:")
    assert get_valid_access_token(store=store, client_id="cid", client_secret="csecret") is None


def test_get_valid_access_token_returns_unexpired_token_without_refreshing(monkeypatch):
    store = Store(":memory:")
    store.save_oauth_connection(
        provider="hubspot", access_token="still-good", refresh_token="rt-1",
        expires_at=time.time() + 1800, account_label="acme.hubspot.com",
    )

    def _boom(*a, **k):
        raise AssertionError("must not refresh a token that's still valid")

    monkeypatch.setattr("sales_cycle.hubspot_oauth._refresh", _boom)
    token = get_valid_access_token(store=store, client_id="cid", client_secret="csecret")
    assert token == "still-good"


@respx.mock
def test_get_valid_access_token_refreshes_an_expired_token():
    store = Store(":memory:")
    store.save_oauth_connection(
        provider="hubspot", access_token="expired", refresh_token="rt-1",
        expires_at=time.time() - 10, account_label="acme.hubspot.com",
    )
    respx.post("https://api.hubapi.com/oauth/v1/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "fresh-token", "refresh_token": "rt-2", "expires_in": 1800,
        })
    )
    token = get_valid_access_token(store=store, client_id="cid", client_secret="csecret")
    assert token == "fresh-token"
    saved = store.get_oauth_connection("hubspot")
    assert saved.refresh_token == "rt-2"


def test_refresh_without_refresh_token_raises():
    store = Store(":memory:")
    store.save_oauth_connection(
        provider="hubspot", access_token="expired", refresh_token=None,
        expires_at=time.time() - 10, account_label=None,
    )
    try:
        get_valid_access_token(store=store, client_id="cid", client_secret="csecret")
        assert False, "expected HubSpotOAuthError"
    except HubSpotOAuthError:
        pass
