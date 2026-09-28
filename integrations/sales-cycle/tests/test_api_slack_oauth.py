import httpx
import respx

from conftest import client


def test_authorize_redirects_to_slack(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI", "http://localhost:8200/oauth/slack/callback")
    resp = client.get("/oauth/slack/authorize", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].startswith("https://slack.com/oauth/v2/authorize?")


def test_authorize_503_when_not_configured(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI", "")
    resp = client.get("/oauth/slack/authorize", follow_redirects=False)
    assert resp.status_code == 503


@respx.mock
def test_callback_success_redirects_and_updates_status(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI", "http://localhost:8200/oauth/slack/callback")
    monkeypatch.setenv("SALES_CYCLE_TERMINAL_URL", "http://localhost:13000")

    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=httpx.Response(200, json={
            "ok": True, "access_token": "xoxb-1", "team": {"id": "T1", "name": "Acme Team"},
        })
    )
    resp = client.get("/oauth/slack/callback?code=the-code", follow_redirects=False)
    assert resp.headers["location"] == "http://localhost:13000/?settings=sales-cycle&slack_connected=1"

    status = client.get("/oauth/slack/status").json()
    assert status == {"connected": True, "configured": True, "account_label": "Acme Team"}


def test_status_not_connected_by_default(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_CLIENT_ID", "")
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI", "")
    assert client.get("/oauth/slack/status").json() == {"connected": False, "configured": False}


def test_status_configured_but_not_connected(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("SALES_CYCLE_SLACK_OAUTH_REDIRECT_URI", "http://localhost:8200/oauth/slack/callback")
    assert client.get("/oauth/slack/status").json() == {"connected": False, "configured": True}


def test_disconnect(monkeypatch, tmp_path):
    import sales_cycle.api as api_module
    store = api_module.get_store()
    store.save_oauth_connection(
        provider="slack", access_token="xoxb-1", refresh_token=None, expires_at=None,
        account_label="Acme Team",
    )
    resp = client.post("/oauth/slack/disconnect")
    assert resp.json() == {"connected": False}
    assert store.get_oauth_connection("slack") is None
