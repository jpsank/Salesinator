import re
from pathlib import Path

import httpx
import respx

import sales_cycle.api as api_module
from conftest import client

AGENT_API = "http://agent-api:8100"


def _fresh_store(monkeypatch, tmp_path: Path):
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))
    monkeypatch.setenv("SALES_CYCLE_AGENT_API_INTERNAL_URL", AGENT_API)
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_SUBJECT", "cust-product-repo")
    return api_module.get_store()


@respx.mock
def test_process_approved_dispatches_then_pushes_then_opens_a_pr(monkeypatch, tmp_path: Path):
    store = _fresh_store(monkeypatch, tmp_path)
    store.record_pending_approval(
        slack_channel="C1", slack_ts="1.1", workspace_id="cust-1",
        source_key="x.md", title="CSV export", body="wants it",
    )
    store.approve(slack_channel="C1", slack_ts="1.1")

    respx.post(f"{AGENT_API}/invocations").mock(
        return_value=httpx.Response(202, json={"workload_id": "agent-1"})
    )
    # The same sweep that dispatches ALSO checks push-readiness for anything already dispatched —
    # including what it just dispatched — so this must be mocked from the very first call.
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    resp = client.post("/internal/process-approved")
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispatched"] == [1]
    assert body["pushed"] == []
    assert body["opened"] == []

    dispatched = store.list_dispatched_unpushed()
    assert len(dispatched) == 1
    branch = dispatched[0].branch
    assert re.fullmatch(r"feature/csv-export-[0-9a-f]{6}", branch)
    assert dispatched[0].workload_id == "agent-1"

    # Not ready yet (still on main) — a second sweep dispatches nothing new, pushes nothing.
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    resp2 = client.post("/internal/process-approved")
    assert resp2.json() == {"dispatched": [], "pushed": [], "opened": [], "timed_out": [], "approved_by_votes": []}

    # Now the turn has finished, landed on the branch, clean tree — this same sweep pushes it AND
    # (list_pushed_unopened is re-queried fresh, so it sees what the push loop just marked) opens
    # its PR, all in one call.
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": branch, "changes": [], "commits": ["a"]})
    )
    respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": branch, "head_sha": "abc"})
    )
    respx.post(f"{AGENT_API}/api/workspace/pull-request").mock(
        return_value=httpx.Response(200, json={"url": "https://github.com/x/y/pull/7", "number": 7})
    )
    resp3 = client.post("/internal/process-approved")
    assert resp3.json() == {"dispatched": [], "pushed": [1], "opened": [1], "timed_out": [], "approved_by_votes": []}
    assert store.list_dispatched_unpushed() == []
    assert store.list_pushed_unopened() == []


@respx.mock
def test_process_approved_dispatch_failure_is_not_fatal(monkeypatch, tmp_path: Path):
    store = _fresh_store(monkeypatch, tmp_path)
    store.record_pending_approval(
        slack_channel="C1", slack_ts="1.1", workspace_id="cust-1",
        source_key="x.md", title="Broken one", body="y",
    )
    store.approve(slack_channel="C1", slack_ts="1.1")

    respx.post(f"{AGENT_API}/invocations").mock(return_value=httpx.Response(500))
    resp = client.post("/internal/process-approved")
    assert resp.status_code == 200
    assert resp.json() == {"dispatched": [], "pushed": [], "opened": [], "timed_out": [], "approved_by_votes": []}
    # Left pending, not dispatched — a later sweep will retry.
    assert store.list_approved_unprocessed()[0].title == "Broken one"


def test_process_approved_noop_when_nothing_pending(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    resp = client.post("/internal/process-approved")
    assert resp.json() == {"dispatched": [], "pushed": [], "opened": [], "timed_out": [], "approved_by_votes": []}


@respx.mock
def test_process_approved_fetches_git_state_per_approval_using_its_own_worktree(monkeypatch, tmp_path: Path):
    """Each approval now has its OWN isolated worktree (core/agent's isolation.mode="worktree"), so
    unlike the old shared-workspace design, git state has to be fetched per approval — a state
    that's ready for one approval's branch says nothing about a DIFFERENT approval's worktree."""
    store = _fresh_store(monkeypatch, tmp_path)
    for i, (ts, branch, unit) in enumerate([("1.1", "feature/a", "unit-a"), ("2.2", "feature/b", "unit-b")]):
        store.record_pending_approval(
            slack_channel="C1", slack_ts=ts, workspace_id="cust-1",
            source_key=f"x{i}.md", title=branch, body="y",
        )
        approved = store.approve(slack_channel="C1", slack_ts=ts)
        store.claim_for_dispatch(approved.id)
        store.mark_dispatched(approved.id, branch=branch, workload_id=unit)

    def git_state_for_unit(request: httpx.Request) -> httpx.Response:
        unit = request.url.params.get("unit")
        ready = {"unit-a": "feature/a", "unit-b": "feature/b"}[unit]
        return httpx.Response(200, json={"branch": ready, "changes": [], "commits": ["a"]})

    respx.get(f"{AGENT_API}/api/workspace/git").mock(side_effect=git_state_for_unit)
    respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/a", "head_sha": "abc"})
    )
    respx.post(f"{AGENT_API}/api/workspace/pull-request").mock(
        return_value=httpx.Response(200, json={"url": "https://github.com/x/y/pull/1", "number": 1})
    )
    resp = client.post("/internal/process-approved")
    # BOTH ready — each read its own worktree's state, not one shared answer.
    assert sorted(resp.json()["pushed"]) == [1, 2]


@respx.mock
def test_process_approved_push_check_failure_is_not_fatal_to_the_sweep(monkeypatch, tmp_path: Path):
    store = _fresh_store(monkeypatch, tmp_path)
    store.record_pending_approval(
        slack_channel="C1", slack_ts="1.1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(approved.id)
    store.mark_dispatched(approved.id, branch="feature/x", workload_id=None)

    respx.get(f"{AGENT_API}/api/workspace/git").mock(return_value=httpx.Response(500))
    resp = client.post("/internal/process-approved")
    assert resp.status_code == 200
    assert resp.json() == {"dispatched": [], "pushed": [], "opened": [], "timed_out": [], "approved_by_votes": []}
    assert store.list_dispatched_unpushed()[0].branch == "feature/x"  # left as-is, retried next sweep


@respx.mock
def test_process_approved_pull_request_failure_leaves_it_pushed_for_retry(monkeypatch, tmp_path: Path):
    store = _fresh_store(monkeypatch, tmp_path)
    store.record_pending_approval(
        slack_channel="C1", slack_ts="1.1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(approved.id)
    store.mark_dispatched(approved.id, branch="feature/x", workload_id=None)
    store.mark_pushed(approved.id)

    respx.post(f"{AGENT_API}/api/workspace/pull-request").mock(return_value=httpx.Response(500))
    resp = client.post("/internal/process-approved")
    assert resp.status_code == 200
    assert resp.json() == {"dispatched": [], "pushed": [], "opened": [], "timed_out": [], "approved_by_votes": []}
    assert store.list_pushed_unopened()[0].id == approved.id  # not marked done — retried next sweep


def test_process_approved_retries_a_stale_dispatch_under_the_attempt_cap(monkeypatch, tmp_path: Path):
    """A 'dispatched' row with no push after the configured timeout is a crashed/hung turn, not one
    still working. Under max_dispatch_attempts it reverts to 'approved' for a fresh retry (a new
    worktree, a new workload_id next sweep) rather than sitting stuck forever."""
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_DISPATCH_TIMEOUT_SEC", "0")
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_MAX_DISPATCH_ATTEMPTS", "2")
    store = _fresh_store(monkeypatch, tmp_path)
    store.record_pending_approval(
        slack_channel="C1", slack_ts="1.1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(approved.id)
    store.mark_dispatched(approved.id, branch="feature/x", workload_id="agent-1")  # attempt 1 of 2

    resp = client.post("/internal/process-approved")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timed_out"] == [{"id": approved.id, "outcome": "retried"}]
    row = store.list_approved_unprocessed()[0]
    assert row.id == approved.id
    assert row.dispatch_attempts == 1  # unchanged by the timeout itself — only a fresh dispatch bumps it


@respx.mock
def test_process_approved_fails_a_stale_dispatch_at_the_attempt_cap(monkeypatch, tmp_path: Path):
    """The SECOND time the same approval's dispatch times out (dispatch_attempts already at the cap
    from the retry's own re-dispatch), it's marked 'failed' for good instead of retrying forever —
    a structurally-broken request must not silently re-spin AI turns against the real repo."""
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_DISPATCH_TIMEOUT_SEC", "0")
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_MAX_DISPATCH_ATTEMPTS", "1")
    store = _fresh_store(monkeypatch, tmp_path)
    store.record_pending_approval(
        slack_channel="C1", slack_ts="1.1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(approved.id)
    store.mark_dispatched(approved.id, branch="feature/x", workload_id="agent-1")  # attempt 1 of 1 (cap)

    resp = client.post("/internal/process-approved")
    assert resp.status_code == 200
    body = resp.json()
    assert body["timed_out"] == [{"id": approved.id, "outcome": "failed"}]
    assert store.list_approved_unprocessed() == []
    assert store.list_dispatched_unpushed() == []
