from pathlib import Path

import httpx
import respx

from sales_cycle import poller
from sales_cycle.poller import poll_once
from sales_cycle.slack_client import SlackClient
from sales_cycle.store import Store


def _write_fr(root: Path, subject: str, slug: str, title: str, body: str) -> None:
    d = root / subject / "kg" / "entities" / "feature_request"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.md").write_text(f"---\ntype: feature_request\nid: {slug}\ntitle: {title}\n---\n{body}\n")


@respx.mock
def test_poll_once_posts_and_records_and_marks_seen(tmp_path: Path):
    _write_fr(tmp_path, "cust-1", "csv-export", "CSV export", "wants it")
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "111.222"})
    )
    store = Store(":memory:")
    slack = SlackClient(bot_token="xoxb-test")

    notified = poll_once(store=store, slack=slack, workspaces_root=tmp_path, channel="C1")

    assert len(notified) == 1
    assert route.called
    pending = store.list_approved_unprocessed()
    assert pending == []  # not approved yet — just recorded as pending
    assert store.is_seen(notified[0])


@respx.mock
def test_poll_once_skips_already_seen(tmp_path: Path):
    _write_fr(tmp_path, "cust-1", "csv-export", "CSV export", "wants it")
    route = respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "111.222"})
    )
    store = Store(":memory:")
    slack = SlackClient(bot_token="xoxb-test")

    first = poll_once(store=store, slack=slack, workspaces_root=tmp_path, channel="C1")
    second = poll_once(store=store, slack=slack, workspaces_root=tmp_path, channel="C1")

    assert len(first) == 1
    assert second == []
    assert route.call_count == 1


@respx.mock
def test_poll_once_slack_failure_does_not_mark_seen_retries_next_sweep(tmp_path: Path):
    _write_fr(tmp_path, "cust-1", "csv-export", "CSV export", "wants it")
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(500)
    )
    store = Store(":memory:")
    slack = SlackClient(bot_token="xoxb-test")

    notified = poll_once(store=store, slack=slack, workspaces_root=tmp_path, channel="C1")

    assert notified == []
    assert store.is_seen(str(tmp_path / "cust-1" / "kg" / "entities" / "feature_request" / "csv-export.md")) is False


@respx.mock
def test_poll_once_never_reads_an_already_seen_file(tmp_path: Path, monkeypatch):
    """The efficiency fix: an already-seen file must be skipped by path alone, never opened again."""
    _write_fr(tmp_path, "cust-1", "csv-export", "CSV export", "wants it")
    respx.post("https://slack.com/api/chat.postMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "ts": "111.222"})
    )
    store = Store(":memory:")
    slack = SlackClient(bot_token="xoxb-test")
    poll_once(store=store, slack=slack, workspaces_root=tmp_path, channel="C1")  # marks it seen

    def _boom(path, workspace_id):
        raise AssertionError(f"should never parse an already-seen file: {path}")

    monkeypatch.setattr(poller, "parse_feature_request_file", _boom)
    second = poll_once(store=store, slack=slack, workspaces_root=tmp_path, channel="C1")
    assert second == []
