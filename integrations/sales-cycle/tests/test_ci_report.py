"""CI's verdict on the agent's pull request is said once in the card's Slack thread: passed, failed (naming the checks), or that no CI ran.

Drives the shipped sweep with GitHub's API and Slack mocked. Reading CI fails open: an unreachable GitHub or a rate limit just waits for the
next sweep, a repository that is not public is said once, and a pull request that was already open before this existed is left alone."""
import json
import sqlite3
import time

import httpx
import pytest
import respx

import sales_cycle.api as api_module
from conftest import client
from sales_cycle.store import Store

SLACK_POST = "https://slack.com/api/chat.postMessage"
PR = "https://github.com/o/r/pull/2"
GH = "https://api.github.com/repos/o/r"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(api_module, "_CI_POLL_EVERY_S", 0.0)


def _opened(*, age_s=0.0, pr=PR):
    store = api_module.get_store()
    store.record_pending_approval(slack_channel="C1", slack_ts="1.1", workspace_id="w", source_key="k", title="CSV", body="b")
    a = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(a.id); store.mark_dispatched(a.id, branch="feature/x", workload_id="u"); store.mark_pushed(a.id); store.mark_done(a.id, pr_url=pr)
    if age_s:
        with store._conn() as c:
            c.execute("UPDATE pending_approvals SET done_at = ? WHERE id = ?", (time.time() - age_s, a.id))
    return store, a


RAW = "https://raw.githubusercontent.com/o/r"
WORKSPACE = 'packages:\n  - "core/services/*"\n  - "clients/terminal"\n'


def _github(runs, *, sha="abc123", files=(), workspace=None):
    """Mocks the GitHub reads: the pull request's head, its check runs, the files it changes, and the repo's pnpm-workspace.yaml (none by default)."""
    respx.get(f"{GH}/pulls/2").mock(return_value=httpx.Response(200, json={"head": {"sha": sha}}))
    respx.get(f"{GH}/pulls/2/files").mock(return_value=httpx.Response(200, json=[{"filename": f} for f in files]))
    respx.get(f"{RAW}/{sha}/pnpm-workspace.yaml").mock(return_value=httpx.Response(404) if workspace is None else httpx.Response(200, text=workspace))
    return respx.get(f"{GH}/commits/{sha}/check-runs").mock(return_value=httpx.Response(200, json={"check_runs": runs}))


def _slack():
    return respx.post(SLACK_POST).mock(return_value=httpx.Response(200, json={"ok": True, "ts": "9.9"}))


def _texts(route):
    return [json.loads(c.request.content)["text"] for c in route.calls]


def _run(name, conclusion="success", status="completed"):
    return {"name": name, "status": status, "conclusion": conclusion}


def _sweep():
    assert client.post("/internal/process-approved").status_code == 200


@respx.mock
def test_passing_checks_are_said_once_in_the_cards_thread():
    store, a = _opened(); _github([_run("static"), _run("node")]); slack = _slack()
    _sweep(); _sweep(); _sweep()
    [text] = _texts(slack)
    assert "CI passed" in text and "2 checks" in text and PR + "/checks" in text
    assert json.loads(slack.calls[0].request.content)["thread_ts"] == "1.1"
    assert store.get_approval(a.id).ci_state == "passed"


@respx.mock
def test_failing_checks_are_named():
    store, a = _opened(); _github([_run("static"), _run("node", "failure")]); slack = _slack()
    _sweep()
    [text] = _texts(slack)
    assert "CI failed" in text and "`node`" in text and "`static`" not in text
    assert store.get_approval(a.id).ci_state == "failed"


@respx.mock
def test_it_waits_while_checks_are_running_and_says_the_verdict_when_they_finish():
    store, a = _opened()
    route = _github([_run("static"), _run("node", None, "in_progress")]); slack = _slack()
    _sweep()
    assert _texts(slack) == [] and store.get_approval(a.id).ci_state is None
    route.mock(return_value=httpx.Response(200, json={"check_runs": [_run("static"), _run("node")]}))
    _sweep()
    assert "CI passed" in _texts(slack)[0]


@respx.mock
def test_the_head_commit_is_looked_up_once():
    store, a = _opened()
    _github([_run("static", None, "queued")]); slack = _slack()
    pulls = respx.routes[0]
    _sweep(); _sweep(); _sweep()
    assert pulls.call_count == 1 and store.get_approval(a.id).pr_head_sha == "abc123"


@respx.mock
def test_with_no_checks_at_all_it_waits_then_says_nothing_ran_ci():
    store, a = _opened(age_s=60); _github([]); slack = _slack()
    _sweep()
    assert _texts(slack) == []                                     # a minute in: CI may simply not have started
    with store._conn() as c:
        c.execute("UPDATE pending_approvals SET done_at = ? WHERE id = ?", (time.time() - 9 * 60, a.id))
    _sweep(); _sweep()
    [text] = _texts(slack)
    assert "No CI check has run" in text and "Actions" in text and store.get_approval(a.id).ci_state == "none"


@respx.mock
def test_a_repository_that_is_not_public_is_said_once_and_not_retried():
    store, a = _opened()
    respx.get(f"{GH}/pulls/2").mock(return_value=httpx.Response(404, json={"message": "Not Found"})); slack = _slack()
    _sweep(); _sweep()
    [text] = _texts(slack)
    assert "isn't public" in text and store.get_approval(a.id).ci_state == "unreadable"


@respx.mock
def test_github_being_unreachable_or_rate_limited_just_waits_for_the_next_sweep():
    store, a = _opened()
    respx.get(f"{GH}/pulls/2").mock(return_value=httpx.Response(403, json={"message": "API rate limit exceeded"})); slack = _slack()
    _sweep()
    assert _texts(slack) == [] and store.get_approval(a.id).ci_state is None
    respx.get(f"{GH}/pulls/2").mock(side_effect=httpx.ConnectError("down"))
    assert client.post("/internal/process-approved").status_code == 200 and _texts(slack) == []


@respx.mock
def test_polling_is_throttled(monkeypatch):
    monkeypatch.setattr(api_module, "_CI_POLL_EVERY_S", 3600.0)
    store, a = _opened(); _github([_run("static")]); slack = _slack()
    _sweep(); _sweep()
    assert _texts(slack) == []                                      # the first look is due an hour after the pull request opened


@respx.mock
def test_a_url_that_is_not_a_github_pull_request_is_left_alone():
    store, a = _opened(pr="https://gitlab.example.com/o/r/-/merge_requests/2"); slack = _slack()
    _sweep()
    assert _texts(slack) == [] and store.get_approval(a.id).ci_state == "untracked"


def test_pull_requests_open_before_this_existed_are_never_given_a_verdict(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE pending_approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, slack_channel TEXT NOT NULL, slack_ts TEXT NOT NULL,
        workspace_id TEXT NOT NULL, source_key TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        branch TEXT, workload_id TEXT, created_at REAL NOT NULL, pr_url TEXT, done_at REAL, UNIQUE(slack_channel, slack_ts))""")
    conn.execute("INSERT INTO pending_approvals (slack_channel, slack_ts, workspace_id, source_key, title, body, status, created_at, pr_url, done_at) "
                 "VALUES ('C','1.1','w','k','T','B','done',1,'https://github.com/o/r/pull/1',1)")
    conn.commit(); conn.close()
    assert Store(path).list_awaiting_ci() == []


# ── a green CI that did not look at what the agent changed ──

@respx.mock
def test_a_pass_says_what_ci_did_not_cover():
    """The real case: CI passed on a pull request whose changed package is outside the pnpm workspace — CI never typechecked it."""
    store, a = _opened()
    _github([_run("node"), _run("static")], files=["packages/transcript-rendering/src/manager.ts", "packages/transcript-rendering/src/index.ts", "README.md"], workspace=WORKSPACE)
    slack = _slack()
    _sweep()
    [text] = _texts(slack)
    assert "CI passed" in text and "does not typecheck or test" in text and "review them by hand" in text
    assert "`packages/transcript-rendering/src/manager.ts`" in text and "`packages/transcript-rendering/src/index.ts`" in text and "README.md" not in text
    assert store.get_approval(a.id).ci_state == "passed"


@respx.mock
def test_a_pass_over_covered_files_is_plain():
    _opened()
    _github([_run("node")], files=["core/services/x/src/a.ts", "clients/terminal/src/b.tsx"], workspace=WORKSPACE)
    slack = _slack()
    _sweep()
    assert "does not typecheck" not in _texts(slack)[0] and "CI passed" in _texts(slack)[0]


@respx.mock
def test_a_repository_without_a_pnpm_workspace_gets_no_coverage_claim():
    _opened()
    _github([_run("node")], files=["packages/x/src/a.ts"], workspace=None)
    slack = _slack()
    _sweep()
    assert "does not typecheck" not in _texts(slack)[0]


@respx.mock
def test_if_the_changed_files_cannot_be_read_the_plain_verdict_still_stands():
    _opened()
    _github([_run("node")], workspace=WORKSPACE)
    respx.get(f"{GH}/pulls/2/files").mock(return_value=httpx.Response(500))
    slack = _slack()
    _sweep()
    assert "CI passed" in _texts(slack)[0] and "does not typecheck" not in _texts(slack)[0]


@respx.mock
def test_a_failure_does_not_make_a_coverage_claim():
    _opened()
    _github([_run("node", "failure")], files=["packages/x/src/a.ts"], workspace=WORKSPACE)
    slack = _slack()
    _sweep()
    assert "CI failed" in _texts(slack)[0] and "does not typecheck" not in _texts(slack)[0]


@respx.mock
def test_upstream_process_checks_failing_do_not_make_the_verdict_a_failure():
    """merge-card (upstream's contribution paperwork) fails on every agent pull request; the verdict is about the code checks."""
    store, a = _opened()
    _github([_run("gates"), _run("node"), _run("merge-card", "failure")], files=["core/services/x/a.ts"], workspace=WORKSPACE)
    slack = _slack()
    _sweep()
    [text] = _texts(slack)
    assert "CI passed" in text and "2 checks" in text and "merge-card" not in text
