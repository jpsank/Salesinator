"""When may a feature request run? The team votes with reactions and a leader gives the go-ahead.

A card in Slack carries 👍 and 👎 for the team and is approved by a leader's ✅ — but only once 👍 outnumber 👎. This module is the
whole decision, over the reactions Slack reports for the card; it does no I/O, so the rules are tested directly. Who counts as a
leader is passed in (``is_leader``), because that needs Slack lookups of its own (see ``approvers.py``).

How reactions are counted:
- the bot's own seeded 👍 and 👎 are never votes;
- a person who reacted both 👍 and 👎 has voted neither way;
- a ✅ from a leader is the go-ahead and is not itself a vote; a ✅ from anyone else counts as a 👍 (it is what people already click);
- a leader's own 👍 or 👎 is a vote like anyone's.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

APPROVE_REACTIONS = frozenset({"white_check_mark", "heavy_check_mark"})
UP_REACTIONS = frozenset({"+1", "thumbsup"})
DOWN_REACTIONS = frozenset({"-1", "thumbsdown"})
VOTE_REACTIONS = APPROVE_REACTIONS | UP_REACTIONS | DOWN_REACTIONS


def base_reaction(name: str) -> str:
    """Slack suffixes a skin tone onto some names ("+1::skin-tone-3"): it is the same reaction."""
    return name.split("::", 1)[0]


def is_vote_reaction(name: str) -> bool:
    return base_reaction(name) in VOTE_REACTIONS


@dataclass(frozen=True)
class Tally:
    up: int
    down: int
    leader_approvers: tuple[str, ...]      # Slack user ids of leaders who reacted ✅, sorted


def tally(reactions: dict[str, list[str]], *, bot_user_id: str, is_leader: Callable[[str], bool]) -> Tally:
    """``reactions`` maps a reaction name to the user ids who used it, as Slack's reactions.get reports them."""
    up: set[str] = set()
    down: set[str] = set()
    checked: set[str] = set()
    for name, users in reactions.items():
        base = base_reaction(name)
        target = up if base in UP_REACTIONS else down if base in DOWN_REACTIONS else checked if base in APPROVE_REACTIONS else None
        if target is None:
            continue
        target.update(u for u in users if u and u != bot_user_id)
    leaders = sorted(u for u in checked if is_leader(u))
    up |= checked - set(leaders)                       # a non-leader's ✅ is a 👍
    both = up & down
    return Tally(up=len(up - both), down=len(down - both), leader_approvers=tuple(leaders))


def decide(t: Tally) -> str:
    """"approve": a leader said go and 👍 outnumber 👎. "waiting": a leader said go, the votes are not there yet. "none": no leader yet."""
    if not t.leader_approvers:
        return "none"
    return "approve" if t.up > t.down else "waiting"
