from pathlib import Path

import httpx
import respx

import sales_cycle.api as api_module
from conftest import client


def _fresh_store(monkeypatch, tmp_path: Path):
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))
    return api_module.get_store()


def test_authorize_redirects_to_hubspot(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI", "http://localhost:8200/oauth/hubspot/callback")

    resp = client.get("/oauth/hubspot/authorize", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].startswith("https://app.hubspot.com/oauth/authorize?")
    assert "client_id=cid" in resp.headers["location"]


def test_authorize_503_when_not_configured(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI", "")
    resp = client.get("/oauth/hubspot/authorize", follow_redirects=False)
    assert resp.status_code == 503


@respx.mock
def test_callback_success_redirects_to_terminal_with_connected_flag(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SALES_CYCLE_HUBSPOT_OAUTH_REDIRECT_URI", "http://localhost:8200/oauth/hubspot/callback")
    monkeypatch.setenv("SALES_CYCLE_TERMINAL_URL", "http://localhost:13000")

    respx.post("https://api.hubapi.com/oauth/v1/token").mock(
        return_value=httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 1800})
    )
    respx.get("https://api.hubapi.com/oauth/v1/access-tokens/at-1").mock(
        return_value=httpx.Response(200, json={"hub_domain": "acme.hubspot.com"})
    )

    resp = client.get("/oauth/hubspot/callback?code=the-code", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "http://localhost:13000/?settings=sales-cycle&hubspot_connected=1"

    status = client.get("/oauth/hubspot/status").json()
    assert status == {"connected": True, "account_label": "acme.hubspot.com"}


def test_callback_with_no_code_redirects_with_error(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SALES_CYCLE_TERMINAL_URL", "http://localhost:13000")
    resp = client.get("/oauth/hubspot/callback", follow_redirects=False)
    assert "hubspot_error=no_code" in resp.headers["location"]


def test_callback_with_hubspot_error_param_redirects_with_that_error(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    monkeypatch.setenv("SALES_CYCLE_TERMINAL_URL", "http://localhost:13000")
    resp = client.get("/oauth/hubspot/callback?error=access_denied", follow_redirects=False)
    assert "hubspot_error=access_denied" in resp.headers["location"]


def test_status_not_connected_by_default(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    assert client.get("/oauth/hubspot/status").json() == {"connected": False}


def test_disconnect(monkeypatch, tmp_path: Path):
    store = _fresh_store(monkeypatch, tmp_path)
    store.save_oauth_connection(
        provider="hubspot", access_token="at", refresh_token="rt", expires_at=None, account_label="x",
    )
    resp = client.post("/oauth/hubspot/disconnect")
    assert resp.json() == {"connected": False}
    assert store.get_oauth_connection("hubspot") is None
