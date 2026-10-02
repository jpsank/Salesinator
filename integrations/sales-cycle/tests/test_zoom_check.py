"""zoom_check — the readiness report for Connect Zoom, against a stand-in public address."""
import hashlib
import hmac
import json

import httpx
import pytest
import respx

from sales_cycle import zoom_check
from sales_cycle.settings import Settings

HOST = "https://sc.example.com"
TOKEN = "zoom-hook-secret"


def settings(**over) -> Settings:
    base = dict(zoom_oauth_client_id="zid", zoom_oauth_client_secret="zsecret", zoom_oauth_redirect_uri=f"{HOST}/oauth/zoom/callback",
                zoom_webhook_secret_token=TOKEN, internal_secret="internal")
    return Settings(**{**base, **over})


def healthy(router: respx.MockRouter, *, token: str = TOKEN) -> None:
    def hook(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["event"] == "endpoint.url_validation":
            plain = body["payload"]["plainToken"]
            return httpx.Response(200, json={"plainToken": plain, "encryptedToken": hmac.new(token.encode(), plain.encode(), hashlib.sha256).hexdigest()})
        return httpx.Response(401, json={"detail": "bad signature"})
    router.post(f"{HOST}/webhooks/zoom").mock(side_effect=hook)
    router.post(f"{HOST}/zoom/disconnect").mock(return_value=httpx.Response(401))


def by_name(checks):
    return {c.name: c for c in checks}


@respx.mock
def test_everything_ready():
    healthy(respx.mock)
    checks = zoom_check.run(settings())
    assert all(c.ok for c in checks), zoom_check.format_report(checks)
    assert "ready" in zoom_check.format_report(checks)


@respx.mock
def test_missing_settings_are_named_without_values():
    healthy(respx.mock)
    c = by_name(zoom_check.run(settings(zoom_oauth_client_secret="")))["settings"]
    assert not c.ok and "SALES_CYCLE_ZOOM_OAUTH_CLIENT_SECRET" in c.detail and "zsecret" not in c.detail


@pytest.mark.parametrize("uri,why", [("http://sc.example.com/oauth/zoom/callback", "https"), (f"{HOST}/oauth/callback", "/oauth/zoom/callback")])
@respx.mock
def test_redirect_address_shape(uri, why):
    healthy(respx.mock)
    c = by_name(zoom_check.run(settings(zoom_oauth_redirect_uri=uri), base=HOST))["redirect address"]
    assert not c.ok and why in c.detail


@respx.mock
def test_a_different_secret_token_than_configured_is_caught():
    healthy(respx.mock, token="some-other-token")
    c = by_name(zoom_check.run(settings()))["webhook handshake"]
    assert not c.ok and "different secret" in c.detail


@respx.mock
def test_an_unreachable_address_is_reported_not_raised():
    respx.mock.post(f"{HOST}/webhooks/zoom").mock(side_effect=httpx.ConnectError("no route"))
    respx.mock.post(f"{HOST}/zoom/disconnect").mock(side_effect=httpx.ConnectError("no route"))
    checks = by_name(zoom_check.run(settings()))
    assert not checks["webhook handshake"].ok and "unreachable" in checks["webhook handshake"].detail


@respx.mock
def test_an_open_webhook_or_open_per_rep_route_fails():
    respx.mock.post(f"{HOST}/webhooks/zoom").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.mock.post(f"{HOST}/zoom/disconnect").mock(return_value=httpx.Response(200, json={}))
    checks = by_name(zoom_check.run(settings()))
    assert not checks["unsigned notification refused"].ok
    assert not checks["per-rep routes need the internal secret"].ok


def test_without_an_address_nothing_is_probed():
    checks = zoom_check.run(settings(zoom_oauth_redirect_uri="", zoom_webhook_secret_token=""))
    assert checks[-1].name == "public address" and not checks[-1].ok
