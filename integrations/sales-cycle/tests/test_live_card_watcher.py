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
def test_a_bookkeeping_failure_on_one_card_does_not_end_the_tail(monkeypatch):
    """The card was already posted to Slack; if recording it fails, the NEXT card must still be handled
    (an escaping exception used to end the watcher, and the replay re-posted the same card forever)."""
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "First"), _card("feature_request", "Second"), _card("feature_request", "First"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(return_value=httpx.Response(200, json={"data": {}}))
    slack_route = respx.post("https://slack.com/api/chat.postMessage").mock(
        side_effect=[httpx.Response(200, json={"ok": True, "ts": "1.1"}), httpx.Response(200, json={"ok": True, "ts": "2.2"})]
    )
    store = Store(":memory:")
    real = store.record_pending_approval
    calls = []
    def flaky(**kw):
        calls.append(kw["title"])
        if kw["title"] == "First":
            raise RuntimeError("database is locked")
        return real(**kw)
    monkeypatch.setattr(store, "record_pending_approval", flaky)

    _run(**_watcher_kwargs(store))

    assert calls == ["First", "Second"]
    assert slack_route.call_count == 2   # the replayed "First" was NOT posted a second time
    assert store.approve(slack_channel="C1", slack_ts="2.2") is not None


def test_recording_a_pending_approval_marks_its_key_seen_atomically():
    store = Store(":memory:")
    store.record_pending_approval(slack_channel="C1", slack_ts="1.1", workspace_id="w", source_key="live:1:x", title="X", body="b")
    assert store.is_seen("live:1:x")
    # a duplicate (channel, ts) violates UNIQUE and must leave NO seen row behind for a different key
    try:
        store.record_pending_approval(slack_channel="C1", slack_ts="1.1", workspace_id="w", source_key="live:1:y", title="Y", body="b")
    except Exception:
        pass
    assert not store.is_seen("live:1:y")


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
def test_not_in_channel_logs_an_actionable_fix_not_just_a_stack_trace(caplog):
    """A Slack error with a KNOWN, human-fixable cause (the app isn't in the channel, a bad channel
    ID, a revoked token, …) will keep failing the SAME way for every future card until someone acts
    on it — the log has to say what to do, not just that chat.postMessage was rejected. It also has
    to reach the TERMINAL, not just this service's logs — set via the meeting-api error field."""
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "CSV export"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "not_in_channel"})
    )
    error_route = respx.put(f"{MEETING_API}/meetings/1/feature-request-post-error").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert store.is_seen("live:1:csv export") is False
    assert "invite @<the app's bot name>" in caplog.text
    assert "not_in_channel" in caplog.text
    assert error_route.called
    sent = json.loads(error_route.calls.last.request.content)
    assert "invite @<the app's bot name>" in sent["error"]


@respx.mock
def test_a_successful_post_clears_any_previously_set_error():
    """A card that later posts successfully means whatever was wrong is fixed — clear the field so
    the terminal doesn't keep showing a stale warning for a problem that's already resolved."""
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "CSV export"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "123.456"})
    )
    error_route = respx.put(f"{MEETING_API}/meetings/1/feature-request-post-error").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert error_route.called
    sent = json.loads(error_route.calls.last.request.content)
    assert sent == {"error": None}


@respx.mock
def test_unrecognized_slack_error_still_logs_without_a_tailored_fix(caplog):
    """An error code this module doesn't recognize gets the ORIGINAL generic message (still worth a
    human's attention, just not one this module can direct with confidence) — never silently dropped."""
    respx.get(STREAM_URL).mock(return_value=httpx.Response(
        200, content=_sse(_card("feature_request", "CSV export"), {"type": "meeting-end"}),
    ))
    respx.get(f"{MEETING_API}/meetings/1").mock(
        return_value=httpx.Response(200, json={"data": {"workspace_id": "cust-42"}})
    )
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "some_new_slack_error"})
    )

    store = Store(":memory:")
    _run(**_watcher_kwargs(store))

    assert store.is_seen("live:1:csv export") is False
    assert "Slack post failed for live card" in caplog.text


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
