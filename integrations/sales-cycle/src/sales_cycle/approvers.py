"""Who may give the go-ahead on a feature request. A leader is anyone who matches ANY of three sources, each optional:

- a configured list of Slack member ids;
- Slack workspace admins and owners (when switched on);
- members of configured Slack user groups.

The policy is kept in ``runtime_settings`` so it can be changed from Vexa's Settings page without a restart. With nothing configured the
policy is "not configured" and the caller keeps the original rule (anyone's ✅ approves) — a live setup is never silently locked.

Slack lookups are cached briefly: one ✅ asks about its reactors, and a vote storm should not become an API storm. A failed lookup means
"not a leader" (it fails closed — nobody gets approval power from an outage) and is logged.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from sales_cycle.slack_client import SlackClient, SlackError

logger = logging.getLogger(__name__)

SETTING_KEY = "slack_approvers"
_USER_ID = re.compile(r"^[UW][A-Z0-9]{2,}$")
_GROUP_ID = re.compile(r"^S[A-Z0-9]{2,}$")


class InvalidPolicy(ValueError):
    pass


@dataclass(frozen=True)
class ApproverPolicy:
    user_ids: frozenset[str] = frozenset()
    include_admins: bool = False
    usergroup_ids: frozenset[str] = frozenset()

    @property
    def configured(self) -> bool:
        return bool(self.user_ids or self.include_admins or self.usergroup_ids)

    def to_dict(self) -> dict:
        return {"user_ids": sorted(self.user_ids), "include_admins": self.include_admins, "usergroup_ids": sorted(self.usergroup_ids)}

    @classmethod
    def from_input(cls, user_ids, include_admins, usergroup_ids) -> "ApproverPolicy":
        """Validates what an admin typed: Slack member ids (U…/W…) and user group ids (S…), nothing else."""
        users = frozenset(str(u).strip().upper() for u in (user_ids or []) if str(u).strip())
        groups = frozenset(str(g).strip().upper() for g in (usergroup_ids or []) if str(g).strip())
        bad = sorted(u for u in users if not _USER_ID.match(u)) + sorted(g for g in groups if not _GROUP_ID.match(g))
        if bad:
            raise InvalidPolicy(f"not a Slack member id (U…) or user group id (S…): {', '.join(bad)}")
        return cls(user_ids=users, include_admins=bool(include_admins), usergroup_ids=groups)


def load_policy(store) -> ApproverPolicy:
    raw = store.get_runtime_setting(SETTING_KEY)
    if not raw:
        return ApproverPolicy()
    try:
        d = json.loads(raw)
        return ApproverPolicy(frozenset(d.get("user_ids") or []), bool(d.get("include_admins")), frozenset(d.get("usergroup_ids") or []))
    except (ValueError, AttributeError):
        logger.warning("slack_approvers setting is not valid JSON — treating approvers as not configured")
        return ApproverPolicy()


def save_policy(store, policy: ApproverPolicy) -> None:
    store.set_runtime_setting(SETTING_KEY, json.dumps(policy.to_dict()))


class LeaderResolver:
    def __init__(self, policy: ApproverPolicy, slack: SlackClient, *, ttl: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._policy, self._slack, self._ttl, self._clock = policy, slack, ttl, clock
        self._admin: dict[str, tuple[float, bool]] = {}
        self._groups: dict[str, tuple[float, frozenset[str]]] = {}

    def is_leader(self, user_id: str) -> bool:
        p = self._policy
        if user_id in p.user_ids:
            return True
        if p.include_admins and self._is_admin(user_id):
            return True
        return any(user_id in self._group(g) for g in sorted(p.usergroup_ids))

    def _is_admin(self, user_id: str) -> bool:
        now = self._clock()
        hit = self._admin.get(user_id)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            u = self._slack.users_info(user=user_id)
            admin = bool(u.get("is_admin") or u.get("is_owner") or u.get("is_primary_owner")) and not u.get("deleted") and not u.get("is_bot")
        except SlackError as e:
            logger.warning("could not check whether %s is a workspace admin (%s) — treating as not a leader", user_id, e.error_code or e)
            return False
        self._admin[user_id] = (now, admin)
        return admin

    def _group(self, group_id: str) -> frozenset[str]:
        now = self._clock()
        hit = self._groups.get(group_id)
        if hit and now - hit[0] < self._ttl:
            return hit[1]
        try:
            members = frozenset(self._slack.usergroup_members(usergroup=group_id))
        except SlackError as e:
            logger.warning("could not list user group %s (%s) — its members are not leaders", group_id, e.error_code or e)
            return frozenset()
        self._groups[group_id] = (now, members)
        return members
