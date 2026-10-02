"""Who counts as a leader: a configured list, workspace admins/owners, or a user group — any one is enough — and what a failed lookup means."""
import httpx
import pytest
import respx

from sales_cycle.approvers import ApproverPolicy, InvalidPolicy, LeaderResolver, load_policy, save_policy
from sales_cycle.slack_client import SlackClient, SlackError
from sales_cycle.store import Store


class FakeSlack:
    def __init__(self, users=None, groups=None, fail=False):
        self.users, self.groups, self.fail, self.calls = users or {}, groups or {}, fail, []

    def users_info(self, *, user):
        self.calls.append(("user", user))
        if self.fail:
            raise SlackError("boom", error_code="missing_scope")
        return self.users.get(user, {})

    def usergroup_members(self, *, usergroup):
        self.calls.append(("group", usergroup))
        if self.fail:
            raise SlackError("boom", error_code="paid_teams_only")
        return self.groups.get(usergroup, [])


def _resolver(policy, slack, now=[0.0]):
    return LeaderResolver(policy, slack, ttl=60, clock=lambda: now[0])


def test_the_configured_list_needs_no_slack_lookup():
    slack = FakeSlack()
    r = _resolver(ApproverPolicy(user_ids=frozenset({"UALICE"})), slack)
    assert r.is_leader("UALICE") and not r.is_leader("UBOB") and slack.calls == []


def test_workspace_admins_and_owners_count_when_switched_on():
    slack = FakeSlack(users={"UADM": {"is_admin": True}, "UOWN": {"is_owner": True}, "UPRIM": {"is_primary_owner": True}, "UBOB": {"is_admin": False}})
    r = _resolver(ApproverPolicy(include_admins=True), slack)
    assert r.is_leader("UADM") and r.is_leader("UOWN") and r.is_leader("UPRIM") and not r.is_leader("UBOB")


def test_admins_do_not_count_when_switched_off():
    slack = FakeSlack(users={"UADM": {"is_admin": True}})
    assert not _resolver(ApproverPolicy(user_ids=frozenset({"UX"})), slack).is_leader("UADM") and slack.calls == []


def test_a_deactivated_admin_or_a_bot_is_not_a_leader():
    slack = FakeSlack(users={"UGONE": {"is_admin": True, "deleted": True}, "UBOT": {"is_admin": True, "is_bot": True}})
    r = _resolver(ApproverPolicy(include_admins=True), slack)
    assert not r.is_leader("UGONE") and not r.is_leader("UBOT")


def test_user_group_members_count():
    slack = FakeSlack(groups={"SLEADS": ["UA", "UB"]})
    r = _resolver(ApproverPolicy(usergroup_ids=frozenset({"SLEADS"})), slack)
    assert r.is_leader("UA") and r.is_leader("UB") and not r.is_leader("UC")


def test_any_one_source_is_enough():
    slack = FakeSlack(users={"UADM": {"is_admin": True}}, groups={"SG": ["UGRP"]})
    r = _resolver(ApproverPolicy(user_ids=frozenset({"ULIST"}), include_admins=True, usergroup_ids=frozenset({"SG"})), slack)
    assert r.is_leader("ULIST") and r.is_leader("UADM") and r.is_leader("UGRP") and not r.is_leader("UNOBODY")


def test_lookups_are_cached_for_a_minute_then_refreshed():
    now = [0.0]
    slack = FakeSlack(users={"UADM": {"is_admin": True}}, groups={"SG": ["UGRP"]})
    r = LeaderResolver(ApproverPolicy(include_admins=True, usergroup_ids=frozenset({"SG"})), slack, ttl=60, clock=lambda: now[0])
    r.is_leader("UADM"); r.is_leader("UADM")                 # the admin lookup is made once
    r.is_leader("UGRP"); r.is_leader("UGRP2")                # the group's member list is fetched once for both
    assert slack.calls.count(("user", "UADM")) == 1 and slack.calls.count(("group", "SG")) == 1
    now[0] = 61.0
    r.is_leader("UADM"); r.is_leader("UGRP")
    assert slack.calls.count(("user", "UADM")) == 2 and slack.calls.count(("group", "SG")) == 2


def test_a_failed_slack_lookup_fails_closed():
    slack = FakeSlack(fail=True)
    r = _resolver(ApproverPolicy(include_admins=True, usergroup_ids=frozenset({"SG"})), slack)
    assert not r.is_leader("UADM")


def test_input_is_validated_and_normalised():
    p = ApproverPolicy.from_input([" ualice ", "U0B", ""], True, ["s123"])
    assert p.user_ids == {"UALICE", "U0B"} and p.usergroup_ids == {"S123"} and p.include_admins and p.configured
    for bad in (["alice"], ["S123"], ["#general"]):
        with pytest.raises(InvalidPolicy):
            ApproverPolicy.from_input(bad, False, [])
    with pytest.raises(InvalidPolicy):
        ApproverPolicy.from_input([], False, ["U123"])


def test_nothing_set_is_not_configured():
    assert not ApproverPolicy().configured and not ApproverPolicy.from_input([], False, []).configured


def test_the_policy_round_trips_through_runtime_settings(tmp_path):
    store = Store(str(tmp_path / "t.db"))
    assert not load_policy(store).configured
    save_policy(store, ApproverPolicy.from_input(["UALICE"], True, ["SG1"]))
    p = load_policy(store)
    assert p.user_ids == {"UALICE"} and p.include_admins and p.usergroup_ids == {"SG1"}
    store.set_runtime_setting("slack_approvers", "{not json")
    assert not load_policy(store).configured


# ── the Slack client calls the vote needs ──

@respx.mock
def test_reactions_get_returns_every_reaction_with_its_users():
    route = respx.get("https://slack.com/api/reactions.get").mock(return_value=httpx.Response(200, json={"ok": True, "message": {"reactions": [
        {"name": "+1", "users": ["UA", "UB"], "count": 2}, {"name": "white_check_mark", "users": ["UL"], "count": 1}]}}))
    got = SlackClient(bot_token="xoxb-t").reactions_get(channel="C1", ts="1.2")
    assert got == {"+1": ["UA", "UB"], "white_check_mark": ["UL"]}
    q = dict(route.calls[0].request.url.params)
    assert q == {"channel": "C1", "timestamp": "1.2", "full": "true"}


@respx.mock
def test_a_message_with_no_reactions_is_an_empty_tally():
    respx.get("https://slack.com/api/reactions.get").mock(return_value=httpx.Response(200, json={"ok": True, "message": {"text": "x"}}))
    assert SlackClient(bot_token="xoxb-t").reactions_get(channel="C1", ts="1.2") == {}


@respx.mock
def test_adding_a_reaction_that_is_already_there_is_fine_but_other_errors_are_not():
    respx.post("https://slack.com/api/reactions.add").mock(side_effect=[
        httpx.Response(200, json={"ok": False, "error": "already_reacted"}),
        httpx.Response(200, json={"ok": False, "error": "missing_scope"})])
    c = SlackClient(bot_token="xoxb-t")
    c.reactions_add(channel="C1", ts="1.2", name="+1")
    with pytest.raises(SlackError) as e:
        c.reactions_add(channel="C1", ts="1.2", name="-1")
    assert e.value.error_code == "missing_scope"


@respx.mock
def test_a_thread_reply_is_a_message_in_the_cards_thread():
    route = respx.post("https://slack.com/api/chat.postMessage").mock(return_value=httpx.Response(200, json={"ok": True, "ts": "9.9"}))
    assert SlackClient(bot_token="xoxb-t").post_thread_reply(channel="C1", thread_ts="1.2", text="hi") == "9.9"
    import json as _j
    assert _j.loads(route.calls[0].request.content) == {"channel": "C1", "thread_ts": "1.2", "text": "hi"}


@respx.mock
def test_user_and_group_lookups_and_auth_test():
    respx.get("https://slack.com/api/users.info").mock(return_value=httpx.Response(200, json={"ok": True, "user": {"id": "U1", "is_admin": True}}))
    respx.get("https://slack.com/api/usergroups.users.list").mock(return_value=httpx.Response(200, json={"ok": True, "users": ["UA", "UB"]}))
    respx.post("https://slack.com/api/auth.test").mock(return_value=httpx.Response(200, json={"ok": True, "user_id": "UBOT"}))
    c = SlackClient(bot_token="xoxb-t")
    assert c.users_info(user="U1")["is_admin"] is True
    assert c.usergroup_members(usergroup="SG") == ["UA", "UB"]
    assert c.auth_test()["user_id"] == "UBOT"
