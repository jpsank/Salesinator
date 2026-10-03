"""The live preview of the agent's pull request: the machine that builds previews asks which pull requests want one, reports how it went, and the
card's thread hears about it once — a link someone on Slack can open, or an honest note that there is nothing to show or it could not be built."""
import json
import sqlite3

import httpx
import pytest
import respx

import sales_cycle.api as api_module
from conftest import client
from sales_cycle.store import Store

SLACK_POST = "https://slack.com/api/chat.postMessage"
PR = "https://github.com/o/r/pull/7"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")


def _opened(pr=PR):
    store = api_module.get_store()
    store.record_pending_approval(slack_channel="C1", slack_ts="1.1", workspace_id="w", source_key="k", title="CSV export", body="b")
    a = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(a.id); store.mark_dispatched(a.id, branch="feature/x", workload_id="u"); store.mark_pushed(a.id); store.mark_done(a.id, pr_url=pr)
    return store, a


def _slack():
    return respx.post(SLACK_POST).mock(return_value=httpx.Response(200, json={"ok": True, "ts": "9.9"}))


def _texts(route):
    return [json.loads(c.request.content)["text"] for c in route.calls]


def test_an_opened_pull_request_is_wanted_until_a_preview_is_reported():
    store, a = _opened()
    assert client.get("/internal/previews/wanted").json() == [{"id": a.id, "pr": 7, "title": "CSV export"}]
    with respx.mock:
        _slack()
        client.post(f"/internal/previews/{a.id}", json={"state": "skipped"})
    assert client.get("/internal/previews/wanted").json() == []


def test_previews_are_for_the_machine_not_the_internet():
    from conftest import bare
    assert bare.get("/internal/previews/wanted").status_code == 401
    assert bare.post("/internal/previews/1", json={"state": "ready"}).status_code == 401


@respx.mock
def test_a_ready_preview_is_announced_once_with_its_link():
    store, a = _opened(); slack = _slack()
    body = {"state": "ready", "url": "https://preview-pr-7.example.com"}
    assert client.post(f"/internal/previews/{a.id}", json=body).json() == {"ok": True, "said": True}
    assert client.post(f"/internal/previews/{a.id}", json=body).json() == {"ok": True, "said": False}
    [text] = _texts(slack)
    assert "<https://preview-pr-7.example.com|Open the preview>" in text and "can't change anything" in text
    assert json.loads(slack.calls[0].request.content)["thread_ts"] == "1.1"
    assert store.get_approval(a.id).preview_url == "https://preview-pr-7.example.com"


@respx.mock
def test_a_preview_only_this_machine_can_open_is_recorded_but_not_announced():
    store, a = _opened(); slack = _slack()
    r = client.post(f"/internal/previews/{a.id}", json={"state": "ready", "url": "http://preview-pr-7.localhost:13100"})
    assert r.json() == {"ok": True, "said": False}
    assert slack.call_count == 0
    assert store.get_approval(a.id).preview_state == "ready"


@respx.mock
@pytest.mark.parametrize("state,words", [("skipped", "No live preview"), ("failed", "Couldn't build a live preview")])
def test_nothing_to_show_and_could_not_build_are_said_plainly(state, words):
    store, a = _opened(); slack = _slack()
    client.post(f"/internal/previews/{a.id}", json={"state": state})
    [text] = _texts(slack)
    assert words in text


def test_an_unknown_request_and_a_made_up_state_are_refused():
    assert client.post("/internal/previews/999", json={"state": "ready"}).status_code == 404
    assert client.post("/internal/previews/1", json={"state": "banana"}).status_code == 422


def test_pull_requests_opened_before_previews_existed_are_not_retrofitted(tmp_path):
    db = str(tmp_path / "old.db")
    store = Store(db)
    store.record_pending_approval(slack_channel="C1", slack_ts="1.1", workspace_id="w", source_key="k", title="Old", body="b")
    a = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(a.id); store.mark_dispatched(a.id, branch="b", workload_id="u"); store.mark_pushed(a.id); store.mark_done(a.id, pr_url=PR)
    with sqlite3.connect(db) as c:                     # the table as it was before the preview columns existed
        c.execute("ALTER TABLE pending_approvals DROP COLUMN preview_state"); c.execute("ALTER TABLE pending_approvals DROP COLUMN preview_url")
    assert Store(db).list_wanting_preview() == []
