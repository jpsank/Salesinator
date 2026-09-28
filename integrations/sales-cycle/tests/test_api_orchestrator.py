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
def test_process_approved_dispatches_then_later_pushes(monkeypatch, tmp_path: Path):
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
    assert len(body["dispatched"]) == 1
    assert body["pushed"] == []

    dispatched = store.list_dispatched_unpushed()
    assert len(dispatched) == 1
    assert dispatched[0].branch == "feature/csv-export"

    # Not ready yet (still on main) — a second sweep dispatches nothing new, pushes nothing.
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    resp2 = client.post("/internal/process-approved")
    assert resp2.json() == {"dispatched": [], "pushed": []}

    # Now the turn has finished, landed on the branch, clean tree — the sweep pushes it.
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/csv-export", "changes": [], "commits": ["a"]})
    )
    respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/csv-export", "head_sha": "abc"})
    )
    resp3 = client.post("/internal/process-approved")
    assert resp3.json() == {"dispatched": [], "pushed": [1]}
    assert store.list_dispatched_unpushed() == []


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
    assert resp.json() == {"dispatched": [], "pushed": []}
    # Left pending, not dispatched — a later sweep will retry.
    assert store.list_approved_unprocessed()[0].title == "Broken one"


def test_process_approved_noop_when_nothing_pending(monkeypatch, tmp_path: Path):
    _fresh_store(monkeypatch, tmp_path)
    resp = client.post("/internal/process-approved")
    assert resp.json() == {"dispatched": [], "pushed": []}


@respx.mock
def test_process_approved_fetches_git_state_once_for_several_dispatched_approvals(monkeypatch, tmp_path: Path):
    """Every dispatched-unpushed approval shares the SAME product_repo_subject workspace, so one
    sweep should read its git state once and reuse it, not once per approval."""
    store = _fresh_store(monkeypatch, tmp_path)
    for i, (ts, branch) in enumerate([("1.1", "feature/a"), ("2.2", "feature/b")]):
        store.record_pending_approval(
            slack_channel="C1", slack_ts=ts, workspace_id="cust-1",
            source_key=f"x{i}.md", title=branch, body="y",
        )
        approved = store.approve(slack_channel="C1", slack_ts=ts)
        store.claim_for_dispatch(approved.id)
        store.mark_dispatched(approved.id, branch=branch, workload_id=None)

    git_route = respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/a", "changes": [], "commits": ["a"]})
    )
    respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/a", "head_sha": "abc"})
    )
    resp = client.post("/internal/process-approved")
    assert resp.json()["pushed"] == [1]  # only the approval on the checked-out branch was ready
    assert git_route.call_count == 1  # two dispatched approvals, one git-state fetch


@respx.mock
def test_process_approved_git_state_failure_does_not_crash_the_sweep(monkeypatch, tmp_path: Path):
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
    assert resp.json() == {"dispatched": [], "pushed": []}
    assert store.list_dispatched_unpushed()[0].branch == "feature/x"  # left as-is, retried next sweep
