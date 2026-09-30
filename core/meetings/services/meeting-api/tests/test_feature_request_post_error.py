"""PUT /meetings/{meeting_id}/feature-request-post-error — SYSTEM-set (not caller-owned like
annotate), reported by an external service (SalesCycle's live card watcher) when a feature_request
card fails to reach Slack for a known, human-fixable reason. Mirrors auto_join_error's shape for a
live row's own failure class.

Drives the collector `create_app` over the in-memory fake, OFFLINE (TestClient, no docker/DB).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_api.collector import create_app
from meeting_api.collector.fakes import InMemoryTranscriptStore

USER = 7
OTHER_USER = 8
H = {"x-user-id": str(USER)}
PLAT, NID = "zoom", "89449735274"


class _NullRedis:
    async def publish(self, channel, data):
        return None


def _client(status="active"):
    """Default status is `active` deliberately: this is reported WHILE a call is live."""
    store = InMemoryTranscriptStore()
    mid = store.seed_meeting(user_id=USER, platform=PLAT, native_meeting_id=NID, status=status)
    return TestClient(create_app(store, redis=_NullRedis())), store, mid


def _set(client, meeting_id, error):
    return client.put(f"/meetings/{meeting_id}/feature-request-post-error", json={"error": error}, headers=H)


def test_sets_the_error_on_a_live_meeting():
    """PATCH answers 409 on a live meeting; this must not — it's reported WHILE the call is
    happening, same reasoning as annotate_meeting."""
    client, store, mid = _client(status="active")
    r = _set(client, mid, "the connected Slack app has not been invited into the target channel")
    assert r.status_code == 200, r.text
    assert store._meetings[mid]["status"] == "active", "setting the error must not touch status"
    assert store._meetings[mid]["data"]["feature_request_post_error"] == (
        "the connected Slack app has not been invited into the target channel"
    )


def test_error_null_clears_it():
    """The watcher clears it the next time a post succeeds — a fixed problem shouldn't leave a
    stale warning behind."""
    client, store, mid = _client()
    _set(client, mid, "not_in_channel: invite the app")
    r = _set(client, mid, None)
    assert r.status_code == 200, r.text
    assert "feature_request_post_error" not in store._meetings[mid]["data"]


def test_empty_string_also_clears_it():
    client, store, mid = _client()
    _set(client, mid, "some error")
    r = _set(client, mid, "   ")
    assert r.status_code == 200, r.text
    assert "feature_request_post_error" not in store._meetings[mid]["data"]


def test_unowned_meeting_is_404_not_leaked():
    client, store, mid = _client()
    r = client.put(
        f"/meetings/{mid}/feature-request-post-error", json={"error": "x"},
        headers={"x-user-id": str(OTHER_USER)},
    )
    assert r.status_code == 404


def test_unknown_meeting_id_is_404():
    client, _store, _mid = _client()
    r = _set(client, 999999, "x")
    assert r.status_code == 404


def test_missing_error_key_is_422():
    client, _store, mid = _client()
    r = client.put(f"/meetings/{mid}/feature-request-post-error", json={}, headers=H)
    assert r.status_code == 422


def test_non_string_error_is_422():
    client, _store, mid = _client()
    r = client.put(f"/meetings/{mid}/feature-request-post-error", json={"error": 123}, headers=H)
    assert r.status_code == 422


def test_round_trips_through_the_list_view():
    """The whole point: this has to reach GET /meetings (the terminal's snapshot source), same as
    auto_join_error already does — not just be readable back from the PUT's own echo."""
    client, _store, mid = _client()
    _set(client, mid, "not_in_channel: invite the app")
    r = client.get("/meetings", headers=H)
    assert r.status_code == 200, r.text
    rows = {m["id"]: m for m in r.json()["meetings"]}
    assert rows[mid]["data"]["feature_request_post_error"] == "not_in_channel: invite the app"
