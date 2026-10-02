"""The Zoom app manifest (zoom-app.manifest.json) is what an operator uploads at marketplace.zoom.us instead of clicking the app
together: it must agree with what this service actually serves and asks for, and it must not make the app public."""
import json
from pathlib import Path
from urllib.parse import urlparse

from sales_cycle.zoom_check import CALLBACK_PATH

MANIFEST = json.loads((Path(__file__).resolve().parent.parent / "zoom-app.manifest.json").read_text())
OAUTH = MANIFEST["oauth_information"]
EVENTS = MANIFEST["features"]["event_subscription"]["events"]


def test_the_redirect_is_the_callback_this_service_serves():
    assert urlparse(OAUTH["development_redirect_uri"]).path == CALLBACK_PATH
    assert OAUTH["development_redirect_uri"] in OAUTH["oauth_allow_list"]


def test_the_webhook_is_this_services_zoom_endpoint_for_meeting_started_only():
    assert [e["event_types"] for e in EVENTS] == [["meeting.started"]]
    assert urlparse(EVENTS[0]["development_webhook_url"]).path == "/webhooks/zoom"


def test_redirect_and_webhook_share_one_https_host():
    hosts = {urlparse(OAUTH["development_redirect_uri"]), urlparse(EVENTS[0]["development_webhook_url"])}
    assert {h.scheme for h in hosts} == {"https"} and len({h.netloc for h in hosts}) == 1


def test_it_asks_for_the_scopes_the_join_flow_reads_and_nothing_more():
    assert {s["scope"] for s in OAUTH["scopes"]} == {"meeting:read:meeting", "user:read:user"}
    assert OAUTH["usage"] == "USER_OPERATION"          # each rep authorizes their own account


def test_the_app_is_not_listed_in_the_marketplace():
    assert MANIFEST["display_information"]["discover_type"] == "UNLISTED"
