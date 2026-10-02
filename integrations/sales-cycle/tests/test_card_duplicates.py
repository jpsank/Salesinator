"""A feature request raised again is noted on the card already posted, not posted as a second card.

Drives the shipped watcher over a faked live feed. The copilot words the same ask differently each time it hears it, so exact-title
matching missed repeats — and a second card invites a second agent run building the same thing."""
import asyncio
import json
import time

import httpx
import respx

from sales_cycle.live_card_watcher import _ordinal, watch_meeting
from sales_cycle.slack_client import SlackClient
from sales_cycle.store import Store

AGENT_API = "http://agent-api:8100"
MEETING_API = "http://meeting-api:8080"
POST = "https://slack.com/api/chat.postMessage"

CSV_A = ("CSV Export of Results", "We should add a button to change the results to CSV for export.")
CSV_B = ("Export results to CSV", "I suggest adding a button to export the results to CSV.")
PDF = ("Export results to PDF", "Add a button to export the results to PDF.")


def _sse(*cards) -> str:
    evs = [{"type": "card", "card": {"kind": "feature_request", "title": t, "body": b, "actionable": True}} for t, b in cards]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in evs + [{"type": "meeting-end"}])


def _watch(store, *cards, meeting="1", workspace="cust-42"):
    respx.get(f"{AGENT_API}/api/meeting/stream?meeting_id={meeting}&session_uid={meeting}").mock(return_value=httpx.Response(200, content=_sse(*cards)))
    respx.get(f"{MEETING_API}/meetings/{meeting}").mock(return_value=httpx.Response(200, json={"data": {"workspace_id": workspace} if workspace else {}}))
    respx.put(f"{MEETING_API}/meetings/{meeting}/feature-request-post-error").mock(return_value=httpx.Response(200, json={}))
    asyncio.run(watch_meeting(agent_api_url=AGENT_API, meeting_api_url=MEETING_API, subject="7", meeting_id=meeting, store=store,
                              slack=SlackClient(bot_token="xoxb-test"), channel="C1", unmapped_slug="unmapped"))


def _slack(*, fail_thread=False):
    n = {"i": 0}
    def post(request):
        body = json.loads(request.content)
        if "thread_ts" in body:
            return httpx.Response(500) if fail_thread else httpx.Response(200, json={"ok": True, "ts": "900.9"})
        n["i"] += 1
        return httpx.Response(200, json={"ok": True, "ts": f"{100 + n['i']}.1"})
    return respx.post(POST).mock(side_effect=post)


def _bodies(route):
    return [json.loads(c.request.content) for c in route.calls]


def _cards(route):
    return [b for b in _bodies(route) if "thread_ts" not in b]


def _replies(route):
    return [b for b in _bodies(route) if "thread_ts" in b]


@respx.mock
def test_the_same_ask_worded_differently_in_one_call_is_noted_on_the_first_card():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, CSV_B)
    assert len(_cards(route)) == 1
    [reply] = _replies(route)
    assert reply["thread_ts"] == "101.1" and reply["channel"] == "C1"
    assert "Raised again" in reply["text"] and "2nd time" in reply["text"] and "Export results to CSV" in reply["text"]
    assert "still waiting for approval" in reply["text"]
    assert store.mention_count(store.known_requests(workspace_id="cust-42", since=0)[0].id) == 2


@respx.mock
def test_a_replayed_card_is_not_noted_twice():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, CSV_B)
    _watch(store, CSV_A, CSV_B)                              # the feed is replayed (a reconnect)
    assert len(_cards(route)) == 1 and len(_replies(route)) == 1


@respx.mock
def test_a_third_mention_counts_up():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, CSV_B, ("CSV export button", "Please add a results export button for CSV."))
    assert len(_cards(route)) == 1 and "3rd time" in _replies(route)[-1]["text"]


@respx.mock
def test_the_same_customer_asking_again_in_a_later_call_is_a_repeat():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, meeting="1")
    _watch(store, CSV_B, meeting="2")
    assert len(_cards(route)) == 1 and len(_replies(route)) == 1


@respx.mock
def test_untagged_calls_are_never_merged_across_calls():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, meeting="1", workspace=None)
    _watch(store, CSV_B, meeting="2", workspace=None)       # a different call in the shared "unmapped" workspace: maybe a different customer
    assert len(_cards(route)) == 2 and _replies(route) == []


@respx.mock
def test_within_one_untagged_call_a_repeat_is_still_a_repeat():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, CSV_B, workspace=None)
    assert len(_cards(route)) == 1 and len(_replies(route)) == 1


@respx.mock
def test_a_different_request_gets_its_own_card():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, PDF)
    assert len(_cards(route)) == 2 and _replies(route) == []


@respx.mock
def test_a_request_whose_build_failed_gets_a_fresh_card_when_asked_again():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, meeting="1")
    first = store.known_requests(workspace_id="cust-42", since=0)[0]
    with store._conn() as c:
        c.execute("UPDATE pending_approvals SET status = 'failed' WHERE id = ?", (first.id,))
    _watch(store, CSV_B, meeting="2")
    assert len(_cards(route)) == 2


@respx.mock
def test_an_old_request_is_not_a_repeat():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, meeting="1")
    with store._conn() as c:
        c.execute("UPDATE pending_approvals SET created_at = ?", (time.time() - 15 * 24 * 3600,))
    _watch(store, CSV_B, meeting="2")
    assert len(_cards(route)) == 2


@respx.mock
def test_the_note_says_where_the_original_stands():
    route = _slack(); store = Store(":memory:")
    _watch(store, CSV_A, meeting="1")
    a = store.known_requests(workspace_id="cust-42", since=0)[0]
    store.approve(slack_channel="C1", slack_ts="101.1")
    store.claim_for_dispatch(a.id); store.mark_dispatched(a.id, branch="feature/x", workload_id="w")
    _watch(store, CSV_B, meeting="2")
    assert "approved and the agent is working on it" in _replies(route)[-1]["text"]
    store.mark_pushed(a.id); store.mark_done(a.id, pr_url="https://github.com/o/r/pull/9")
    _watch(store, ("Results CSV export", "Button for exporting results as CSV."), meeting="3")
    assert "done: https://github.com/o/r/pull/9" in _replies(route)[-1]["text"]


@respx.mock
def test_a_failed_thread_reply_still_records_the_repeat_and_does_not_end_the_watcher():
    route = _slack(fail_thread=True); store = Store(":memory:")
    _watch(store, CSV_A, CSV_B, PDF)                         # the PDF card after the failing note must still be posted
    assert len(_cards(route)) == 2
    assert store.mention_count(store.known_requests(workspace_id="cust-42", since=0)[0].id) == 2


def test_ordinals():
    assert [_ordinal(n) for n in (2, 3, 4, 11, 12, 13, 21, 22)] == ["2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd"]


def test_the_store_filters_known_requests_by_workspace_age_failure_and_call(tmp_path):
    s = Store(str(tmp_path / "k.db"))
    for ts, ws, key in (("1.1", "w1", "live:1:a"), ("2.1", "w1", "live:2:b"), ("3.1", "w2", "live:1:c")):
        s.record_pending_approval(slack_channel="C", slack_ts=ts, workspace_id=ws, source_key=key, title=key, body="")
    assert [a.source_key for a in s.known_requests(workspace_id="w1", since=0)] == ["live:1:a", "live:2:b"]
    assert [a.source_key for a in s.known_requests(workspace_id="w1", since=0, source_prefix="live:2:")] == ["live:2:b"]
    assert s.known_requests(workspace_id="w1", since=time.time() + 60) == []
    with s._conn() as c:
        c.execute("UPDATE pending_approvals SET status = 'failed' WHERE source_key = 'live:1:a'")
    assert [a.source_key for a in s.known_requests(workspace_id="w1", since=0)] == ["live:2:b"]
