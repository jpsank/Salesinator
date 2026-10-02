"""The voting rule: a leader's ✅ approves a feature request only once 👍 outnumber 👎, over Slack's reactions for the card."""
from sales_cycle.approval_policy import Tally, decide, is_vote_reaction, tally

BOT = "UBOT"
LEADERS = {"ULEAD", "ULEAD2"}


def _t(reactions):
    return tally(reactions, bot_user_id=BOT, is_leader=lambda u: u in LEADERS)


def test_a_leader_approval_with_more_up_than_down_approves():
    t = _t({"white_check_mark": ["ULEAD"], "+1": ["UA", "UB"], "-1": ["UC"]})
    assert (t.up, t.down, t.leader_approvers) == (2, 1, ("ULEAD",))
    assert decide(t) == "approve"


def test_the_bots_own_seeded_reactions_are_not_votes():
    t = _t({"white_check_mark": ["ULEAD"], "+1": [BOT], "-1": [BOT]})
    assert (t.up, t.down) == (0, 0)
    assert decide(t) == "waiting"                  # a leader said go, but nobody has voted for it


def test_equal_votes_are_not_enough():
    assert decide(_t({"white_check_mark": ["ULEAD"], "+1": ["UA"], "-1": ["UB"]})) == "waiting"


def test_more_down_than_up_waits():
    assert decide(_t({"white_check_mark": ["ULEAD"], "+1": ["UA"], "-1": ["UB", "UC"]})) == "waiting"


def test_without_a_leader_nothing_happens_however_many_votes():
    t = _t({"+1": ["UA", "UB", "UC"], "white_check_mark": ["UD"]})
    assert t.leader_approvers == () and decide(t) == "none"


def test_a_non_leaders_check_counts_as_an_up_vote_but_never_approves():
    t = _t({"white_check_mark": ["UD", "ULEAD"], "+1": ["UA"]})
    assert (t.up, t.leader_approvers) == (2, ("ULEAD",))
    assert decide(t) == "approve"


def test_a_leaders_check_is_not_itself_a_vote():
    t = _t({"white_check_mark": ["ULEAD", "ULEAD2"]})
    assert (t.up, t.down) == (0, 0) and t.leader_approvers == ("ULEAD", "ULEAD2")
    assert decide(t) == "waiting"


def test_a_leaders_own_thumbs_are_votes_like_anyones():
    assert decide(_t({"white_check_mark": ["ULEAD"], "+1": ["ULEAD2"]})) == "approve"


def test_someone_who_reacted_both_ways_has_voted_neither():
    t = _t({"white_check_mark": ["ULEAD"], "+1": ["UA", "UB"], "-1": ["UB"]})
    assert (t.up, t.down) == (1, 0)
    assert decide(t) == "approve"
    t2 = _t({"white_check_mark": ["ULEAD"], "+1": ["UA"], "-1": ["UA"]})
    assert (t2.up, t2.down) == (0, 0) and decide(t2) == "waiting"


def test_a_non_leader_who_checks_and_thumbs_down_has_voted_neither():
    t = _t({"white_check_mark": ["ULEAD", "UD"], "-1": ["UD"], "+1": ["UA"]})
    assert (t.up, t.down) == (1, 0)


def test_skin_tone_variants_are_the_same_reaction():
    t = _t({"white_check_mark": ["ULEAD"], "+1::skin-tone-3": ["UA"], "-1::skin-tone-2": ["UB", "UC"]})
    assert (t.up, t.down) == (1, 2)


def test_other_reactions_are_ignored():
    t = _t({"eyes": ["UA", "UB"], "tada": ["UC"], "white_check_mark": ["ULEAD"], "+1": ["UD"]})
    assert (t.up, t.down) == (1, 0)


def test_which_reactions_matter():
    assert all(is_vote_reaction(n) for n in ("white_check_mark", "heavy_check_mark", "+1", "-1", "+1::skin-tone-2"))
    assert not any(is_vote_reaction(n) for n in ("eyes", "tada", "heart"))


def test_the_leader_lookup_is_only_asked_about_people_who_checked():
    asked = []
    tally({"+1": ["UA", "UB"], "white_check_mark": ["UC"]}, bot_user_id=BOT, is_leader=lambda u: asked.append(u) or False)
    assert asked == ["UC"]


def test_decide_on_a_hand_built_tally():
    assert decide(Tally(up=1, down=0, leader_approvers=("U1",))) == "approve"
    assert decide(Tally(up=5, down=0, leader_approvers=())) == "none"
