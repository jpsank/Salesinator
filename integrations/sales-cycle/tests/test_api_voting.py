"""Feature requests are approved by the team's votes and a leader's go-ahead: a leader's ✅ approves only once 👍 outnumber 👎.

Drives the shipped /slack/events endpoint (signed as Slack signs it) with Slack's Web API mocked: the reactions are recounted from
Slack, never trusted from the event."""
import hashlib
import hmac
import json
import re
import time

import httpx
import pytest
import respx

import sales_cycle.api as api_module
from conftest import bare, client
from sales_cycle.approvers import ApproverPolicy, save_policy
from sales_cycle.live_card_watcher import _format_message, _seed_votes
from sales_cycle.slack_client import SlackClient

SECRET = "test-signing-secret"
CH, TS = "C1", "100.001"
SLACK = "https://slack.com/api"


@pytest.fixture(autouse=True)
def _slack_env(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SALES_CYCLE_AGENT_API_INTERNAL_URL", "http://agent-api:8100")
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_SUBJECT", "product-repo")
    api_module._bot_user_ids.clear()


def _event(kind: str, reaction: str, user: str = "UX") -> httpx.Response:
    body = json.dumps({"type": "event_callback", "event": {"type": kind, "reaction": reaction, "user": user,
                                                          "item": {"channel": CH, "ts": TS}}}).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
    return client.post("/slack/events", content=body, headers={"Content-Type": "application/json",
                                                              "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


def _card(store):
    store.record_pending_approval(slack_channel=CH, slack_ts=TS, workspace_id="cust-1", source_key="x.md", title="CSV export", body="wants it")


def _leaders(store, *ids, admins=False, groups=()):
    save_policy(store, ApproverPolicy.from_input(list(ids), admins, list(groups)))


def _slack(reactions: dict[str, list[str]], *, bot="UBOT"):
    respx.post(f"{SLACK}/auth.test").mock(return_value=httpx.Response(200, json={"ok": True, "user_id": bot}))
    respx.get(f"{SLACK}/reactions.get").mock(return_value=httpx.Response(200, json={"ok": True, "message": {"reactions": [
        {"name": n, "users": u, "count": len(u)} for n, u in reactions.items()]}}))
    return respx.post(f"{SLACK}/chat.postMessage").mock(return_value=httpx.Response(200, json={"ok": True, "ts": "200.1"}))


def _agent():
    return respx.post("http://agent-api:8100/invocations").mock(return_value=httpx.Response(202, json={"workload_id": "agent-1"}))


def _replies(route) -> list[str]:
    return [json.loads(c.request.content)["text"] for c in route.calls]


@respx.mock
def test_a_leaders_check_with_more_up_than_down_approves_and_starts_the_agent():
    store = api_module.get_store(); _card(store); _leaders(store, "ULEAD")
    posted = _slack({"white_check_mark": ["ULEAD"], "+1": ["UBOT", "UA", "UB"], "-1": ["UBOT", "UC"]})
    agent = _agent()
    assert _event("reaction_added", "white_check_mark", "ULEAD").status_code == 200
    d = store.list_dispatched_unpushed()
    assert len(d) == 1 and d[0].approved_by == "ULEAD" and (d[0].votes_up, d[0].votes_down) == (2, 1)
    assert agent.called
    assert len(_replies(posted)) == 1 and "<@ULEAD>" in _replies(posted)[0] and "2 :+1: / 1 :-1:" in _replies(posted)[0]
    assert json.loads(posted.calls[0].request.content)["thread_ts"] == TS


@respx.mock
def test_a_leaders_check_without_the_votes_waits_and_says_so_once():
    store = api_module.get_store(); _card(store); _leaders(store, "ULEAD")
    posted = _slack({"white_check_mark": ["ULEAD"], "+1": ["UBOT"], "-1": ["UBOT"]})      # only the bot's own seeds
    agent = _agent()
    _event("reaction_added", "white_check_mark", "ULEAD")
    _event("reaction_added", "white_check_mark", "ULEAD")
    assert store.list_approved_unprocessed() == [] and store.list_dispatched_unpushed() == [] and not agent.called
    assert store.pending_for_message(slack_channel=CH, slack_ts=TS) is not None
    assert len(_replies(posted)) == 1 and "needs more :+1: than :-1:" in _replies(posted)[0]


@respx.mock
def test_it_approves_on_its_own_when_the_votes_tip_after_the_leader_said_go():
    store = api_module.get_store(); _card(store); _leaders(store, "ULEAD")
    _slack({"white_check_mark": ["ULEAD"], "+1": ["UBOT"], "-1": ["UBOT"]})
    _event("reaction_added", "white_check_mark", "ULEAD")
    assert store.list_dispatched_unpushed() == []
    respx.reset()
    posted = _slack({"white_check_mark": ["ULEAD"], "+1": ["UBOT", "UA"], "-1": ["UBOT"]})
    _agent()
    _event("reaction_added", "+1", "UA")
    d = store.list_dispatched_unpushed()
    assert len(d) == 1 and (d[0].votes_up, d[0].votes_down) == (1, 0) and "Approved by <@ULEAD>" in _replies(posted)[0]


@respx.mock
def test_the_sweep_approves_a_waiting_request_whose_votes_tipped_without_an_event():
    store = api_module.get_store(); _card(store); _leaders(store, "ULEAD")
    _slack({"white_check_mark": ["ULEAD"], "+1": ["UBOT"], "-1": ["UBOT", "UC"]})
    _event("reaction_added", "white_check_mark", "ULEAD")
    assert store.list_dispatched_unpushed() == []
    respx.reset()
    _slack({"white_check_mark": ["ULEAD"], "+1": ["UBOT", "UA"], "-1": ["UBOT"]})      # the 👎 was taken back and a 👍 added: no event arrived
    _agent()
    respx.get("http://agent-api:8100/api/workspace/git").mock(return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []}))
    swept = client.post("/internal/process-approved").json()
    assert len(swept["approved_by_votes"]) == 1 and len(store.list_dispatched_unpushed()) == 1


@respx.mock
def test_a_non_leaders_check_never_approves_it_only_counts_as_a_vote():
    store = api_module.get_store(); _card(store); _leaders(store, "ULEAD")
    posted = _slack({"white_check_mark": ["UNOBODY", "UOTHER"], "+1": ["UBOT"]})
    agent = _agent()
    _event("reaction_added", "white_check_mark", "UNOBODY")
    assert store.list_dispatched_unpushed() == [] and not agent.called and posted.call_count == 0
    assert store.pending_for_message(slack_channel=CH, slack_ts=TS) is not None


@respx.mock
def test_workspace_admins_count_as_leaders_when_switched_on():
    store = api_module.get_store(); _card(store); _leaders(store, admins=True)
    _slack({"white_check_mark": ["UADMIN"], "+1": ["UA"]})
    respx.get(f"{SLACK}/users.info").mock(return_value=httpx.Response(200, json={"ok": True, "user": {"id": "UADMIN", "is_admin": True}}))
    _agent()
    _event("reaction_added", "white_check_mark", "UADMIN")
    assert len(store.list_dispatched_unpushed()) == 1


@respx.mock
def test_user_group_members_count_as_leaders():
    store = api_module.get_store(); _card(store); _leaders(store, groups=["SLEADS"])
    _slack({"white_check_mark": ["UG"], "+1": ["UA"]})
    respx.get(f"{SLACK}/usergroups.users.list").mock(return_value=httpx.Response(200, json={"ok": True, "users": ["UG"]}))
    _agent()
    _event("reaction_added", "white_check_mark", "UG")
    assert len(store.list_dispatched_unpushed()) == 1


@respx.mock
def test_when_slack_cannot_be_read_nothing_is_approved():
    store = api_module.get_store(); _card(store); _leaders(store, "ULEAD")
    respx.post(f"{SLACK}/auth.test").mock(return_value=httpx.Response(200, json={"ok": True, "user_id": "UBOT"}))
    respx.get(f"{SLACK}/reactions.get").mock(return_value=httpx.Response(200, json={"ok": False, "error": "missing_scope"}))
    agent = _agent()
    assert _event("reaction_added", "white_check_mark", "ULEAD").status_code == 200
    assert store.list_dispatched_unpushed() == [] and not agent.called


@respx.mock
def test_a_reaction_on_some_other_message_is_ignored():
    store = api_module.get_store(); _leaders(store, "ULEAD")
    _slack({"white_check_mark": ["ULEAD"], "+1": ["UA"]})
    agent = _agent()
    _event("reaction_added", "white_check_mark", "ULEAD")           # no card recorded for this message
    assert not agent.called


@respx.mock
def test_without_approvers_the_original_rule_stands_and_votes_do_nothing():
    store = api_module.get_store(); _card(store)
    agent = _agent()
    _event("reaction_added", "+1", "UA")
    assert not agent.called and store.pending_for_message(slack_channel=CH, slack_ts=TS) is not None
    _event("reaction_added", "white_check_mark", "UANYONE")           # anyone's ✅ approves, as before
    d = store.list_dispatched_unpushed()
    assert len(d) == 1 and d[0].approved_by is None and agent.called


# ── settings ──

def test_the_approvers_settings_round_trip_and_are_validated():
    assert client.get("/slack/approvers").json() == {"user_ids": [], "include_admins": False, "usergroup_ids": [], "configured": False}
    r = client.post("/slack/approvers", json={"user_ids": ["ualice", "U0B"], "include_admins": True, "usergroup_ids": ["s1abc"]})
    assert r.status_code == 200 and r.json() == {"user_ids": ["U0B", "UALICE"], "include_admins": True, "usergroup_ids": ["S1ABC"], "configured": True}
    assert client.get("/slack/approvers").json()["configured"] is True
    assert client.post("/slack/approvers", json={"user_ids": ["alice"]}).status_code == 422
    assert client.post("/slack/approvers", json={"user_ids": [], "include_admins": False, "usergroup_ids": []}).json()["configured"] is False


def test_the_approvers_settings_are_private():
    assert bare.get("/slack/approvers").status_code == 401
    assert bare.post("/slack/approvers", json={"include_admins": True}).status_code == 401


# ── the card ──

def test_the_card_says_how_to_vote_only_when_approvers_are_configured():
    assert "Vote with :+1: / :-1:" in _format_message("w", "T", "B", voting=True) and "leader's :white_check_mark:" in _format_message("w", "T", "B", voting=True)
    legacy = _format_message("w", "T", "B")
    assert "React :white_check_mark: to approve" in legacy and "Vote with" not in legacy


@respx.mock
def test_the_bot_seeds_thumbs_up_and_down_on_the_card():
    route = respx.post(f"{SLACK}/reactions.add").mock(return_value=httpx.Response(200, json={"ok": True}))
    _seed_votes(SlackClient(bot_token="xoxb-t"), CH, TS)
    assert [json.loads(c.request.content)["name"] for c in route.calls] == ["+1", "-1"]


@respx.mock
def test_a_missing_reactions_write_scope_does_not_break_the_card():
    respx.post(f"{SLACK}/reactions.add").mock(return_value=httpx.Response(200, json={"ok": False, "error": "missing_scope"}))
    _seed_votes(SlackClient(bot_token="xoxb-t"), CH, TS)           # logged, not raised
