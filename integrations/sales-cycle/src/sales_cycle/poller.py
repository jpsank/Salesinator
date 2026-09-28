"""Notice new feature_request entities and post one Slack message per request (never bundled — a
message must map to exactly one approvable item, or a single ✅ can't tell us which one was approved).
"""

from __future__ import annotations

import logging
from pathlib import Path

from sales_cycle.entity_files import find_feature_request_entities
from sales_cycle.slack_client import SlackClient, SlackError
from sales_cycle.store import Store

logger = logging.getLogger("sales_cycle.poller")


def _format_message(workspace_id: str, title: str, body: str) -> str:
    return (
        f":bulb: *Feature request* — `{workspace_id}`\n"
        f"*{title}*\n"
        f"{body}\n\n"
        f"React :white_check_mark: to approve — an agent will implement it on a branch and push it."
    )


def poll_once(*, store: Store, slack: SlackClient, workspaces_root: Path, channel: str) -> list[str]:
    """Returns the paths newly notified this pass. Never raises — a single Slack failure logs and
    moves on to the next entity rather than blocking the whole sweep (P18: report, don't crash)."""
    notified: list[str] = []
    for entity in find_feature_request_entities(workspaces_root):
        key = str(entity.path)
        if store.is_seen(key):
            continue
        try:
            ts = slack.post_message(channel=channel, text=_format_message(entity.workspace_id, entity.title, entity.body))
        except SlackError:
            logger.exception("Slack post failed for %s — will retry next sweep", key)
            continue  # NOT marked seen — retry next poll rather than losing it silently
        store.record_pending_approval(
            slack_channel=channel, slack_ts=ts, workspace_id=entity.workspace_id,
            entity_path=key, title=entity.title, body=entity.body,
        )
        store.mark_seen(key)
        notified.append(key)
    return notified
