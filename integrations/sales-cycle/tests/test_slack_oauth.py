import httpx
import respx

from sales_cycle.slack_oauth import SlackOAuthError, build_authorize_url, exchange_code, get_access_token
from sales_cycle.store import Store


def test_build_authorize_url_includes_client_id_and_redirect_and_scopes():
    url = build_authorize_url(
        client_id="abc123", redirect_uri="http://localhost:8200/oauth/slack/callback",
        scopes="chat:write,channels:read",
    )
    assert url.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=abc123" in url
    assert "scope=chat%3Awrite%2Cchannels%3Aread" in url


@respx.mock
def test_exchange_code_saves_connection_with_team_name():
    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=httpx.Response(200, json={
            "ok": True, "access_token": "xoxb-1", "team": {"id": "T1", "name": "Acme Team"},
        })
    )
    store = Store(":memory:")
    connection = exchange_code(
        store=store, client_id="cid", client_secret="csecret",
        redirect_uri="http://localhost:8200/oauth/slack/callback", code="the-code",
    )
    assert connection.access_token == "xoxb-1"
    assert connection.account_label == "Acme Team"
    assert connection.refresh_token is None
    assert connection.expires_at is None


@respx.mock
def test_exchange_code_slack_ok_false_raises():
    respx.post("https://slack.com/api/oauth.v2.access").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "invalid_code"})
    )
    store = Store(":memory:")
    try:
        exchange_code(
            store=store, client_id="cid", client_secret="csecret",
            redirect_uri="http://localhost:8200/oauth/slack/callback", code="bad-code",
        )
        assert False, "expected SlackOAuthError"
    except SlackOAuthError as e:
        assert "invalid_code" in str(e)


def test_get_access_token_none_when_never_connected():
    store = Store(":memory:")
    assert get_access_token(store=store) is None


def test_get_access_token_returns_stored_token():
    store = Store(":memory:")
    store.save_oauth_connection(
        provider="slack", access_token="xoxb-1", refresh_token=None, expires_at=None,
        account_label="Acme Team",
    )
    assert get_access_token(store=store) == "xoxb-1"
