import asyncio
import json

import httpx
import respx

import sales_cycle.live_card_watcher as watcher_module
from sales_cycle.slack_client import SlackClient
from sales_cycle.store import Store
from sales_cycle.live_card_watcher import watch_meeting

AGENT_API = "http://agent-api:8100"
MEETING_API = "http://meeting-api:8080"
STREAM_URL = f"{AGENT_API}/api/meeting/stream?meeting_id=1&session_uid=1"


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events)


def _card(kind: str, title: str, body: str = "wants it") -> dict:
    return {"type": "card", "card": {"kind": kind, "title": title, "body": body, "actionable": True}}


def _run(**kwargs) -> None:
    asyncio.run(watch_meeting(**kwargs))


def _watcher_kwargs(store: Store, **overrides) -> dict:
    base = dict(
        agent_api_url=AGENT_API, meeting_api_url=MEETING_API, subject="7", meeting_id="1",
        store=store, slack=SlackClient(bot_token="xoxb-test"), channel="C1", unmapped_slug="unmapped",
    )
    base.update(overrides)
    return base


@respx.mock
def test_posts_feature_request_card_and_records_pending_approval():
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "CSV export"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "100.1"})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert slack_route.called
    posted = json.loads(slack_route.calls.last.request.content)
    assert "CSV export" in posted["text"]
    assert "cust-42" in posted["text"]

    pending = store.list_approved_unprocessed()
    assert pending == []  # not yet approved — just recorded, same as the old poll path
    approved = store.approve(slack_channel="C1", slack_ts="100.1")
    assert approved is not None
    assert approved.workspace_id == "cust-42"
    assert approved.title == "CSV export"


@respx.mock
def test_ignores_non_feature_request_cards():
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("note", "irrelevant"), {"type": "meeting-end"}),
    ))
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert not slack_route.called


@respx.mock
def test_dedupes_repeated_card_within_one_call():
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(
            _card("feature_request", "CSV export"), _card("feature_request", "CSV export"),
            {"type": "meeting-end"},
        ),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert slack_route.call_count == 1
    assert store.is_seen("live:1:csv export") is True


@respx.mock
def test_workspace_lookup_failure_falls_back_to_unmapped_slug():
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "CSV export"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(return_value=httpx.Response(500))
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    posted = json.loads(slack_route.calls.last.request.content)
    assert "unmapped" in posted["text"]


@respx.mock
def test_slack_failure_does_not_mark_seen_so_a_later_reoccurrence_can_retry():
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "CSV export"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(return_value=httpx.Response(500))

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))  # must not raise — a background task never crashes the process

    assert store.is_seen("live:1:csv export") is False


@respx.mock
def test_never_raises_when_the_stream_itself_is_unreachable(monkeypatch):
    monkeypatch.setattr(watcher_module, "_RECONNECT_DELAY_SEC", 0)
    monkeypatch.setattr(watcher_module, "_MAX_RECONNECTS", 1)
    respx.get(STREAM_URL).mock(side_effect=httpx.ConnectError("refused"))

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))  # must return quietly, not raise


@respx.mock
def test_permanent_rejection_is_not_retried(monkeypatch):
    """A 403 (not authorized for this meeting) or 404 (unknown meeting) is a permanent answer, not a
    blip — retrying it would just waste a minute reconnecting to hear the same 'no' again."""
    monkeypatch.setattr(watcher_module, "_RECONNECT_DELAY_SEC", 999)  # would hang the test if retried
    route = respx.get(STREAM_URL).mock(return_value=httpx.Response(403))

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))  # must return promptly, not sleep 999s waiting to retry

    assert route.call_count == 1


@respx.mock
def test_sse_frame_spanning_multiple_data_lines_is_reassembled():
    """The SSE spec allows a frame's payload to be split across several `data:` lines, joined before
    parsing — a single-line `data: {...}` frame (what agent-api's own feed always sends today) is
    just the common case of this, not the only legal shape."""
    body = 'data: {"type": "card", "card": {"kind":\ndata:  "feature_request", "title": "CSV export"}}\n\n'
    body += f'data: {json.dumps({"type": "meeting-end"})}\n\n'
    respx.get(STREAM_URL).mock(return_value=httpx.Response(200, content=body))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "1.1"})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    posted = json.loads(slack_route.calls.last.request.content)
    assert "CSV export" in posted["text"]


@respx.mock
def test_workspace_lookup_is_cached_across_a_burst_of_cards():
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(
            _card("feature_request", "CSV export"), _card("feature_request", "SSO login"),
            {"type": "meeting-end"},
        ),
    ))
    workspace_route = respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    ts_values = iter(["1.1", "1.2"])
    respx.post("https://slack.com/api/chat.postMessage").mock(
        side_effect=lambda request: httpx.Response(200, json={"ok": True, "ts": next(ts_values)})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert workspace_route.call_count == 1  # two distinct cards, one lookup — the TTL cache held
