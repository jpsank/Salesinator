import json
import re

import httpx
import pytest
import respx

from sales_cycle.orchestrator import (
    DispatchError, PullRequestError, PushError,
    branch_for, check_and_push, git_state, open_pull_request, push_if_ready, slug_for,
    submit_implementation,
)

AGENT_API = "http://agent-api:8100"


@pytest.mark.parametrize("title,expected", [
    ("CSV Export for Reports", "csv-export-for-reports"),
    ("SSO / SAML support!!", "sso-saml-support"),
    ("!!!", "feature"),
])
def test_slug_for(title, expected):
    assert slug_for(title) == expected


def test_branch_for_has_feature_prefix_and_a_per_attempt_suffix():
    assert branch_for("CSV export", "ab12cd") == "feature/csv-export-ab12cd"


@respx.mock
def test_submit_implementation_posts_invocation_and_returns_branch():
    route = respx.post(f"{AGENT_API}/invocations").mock(
        return_value=httpx.Response(202, json={"workload_id": "agent-123"})
    )
    result = submit_implementation(
        agent_api_url=AGENT_API, subject="cust-1", title="CSV export", body="wants it",
    )
    assert result["workload_id"] == "agent-123"
    assert re.fullmatch(r"feature/csv-export-[0-9a-f]{6}", result["branch"])
    sent = route.calls[0].request
    assert sent.headers["X-User-Id"] == "cust-1"
    body = json.loads(sent.content)
    assert body["identity"]["subject"] == "cust-1"
    assert "identity" in body and "principal" not in body["identity"]  # no signoff configured
    assert body["workspaces"] == [{"id": "cust-1", "mode": "rw"}]
    assert body["isolation"] == {"mode": "worktree"}
    assert f"git checkout -b {result['branch']}" in body["start"]["entrypoint"]["inline"]
    assert "Co-Authored-By" in body["start"]["entrypoint"]["inline"]  # instructed NOT to add one


@respx.mock
def test_submit_implementation_raises_on_failure():
    respx.post(f"{AGENT_API}/invocations").mock(return_value=httpx.Response(500))
    with pytest.raises(DispatchError):
        submit_implementation(agent_api_url=AGENT_API, subject="cust-1", title="X", body="y")


@respx.mock
def test_check_and_push_not_ready_wrong_branch():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    assert check_and_push(agent_api_url=AGENT_API, subject="cust-1", expected_branch="feature/csv-export") is None


@respx.mock
def test_check_and_push_not_ready_dirty_tree():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/csv-export", "changes": ["a.py"], "commits": []})
    )
    assert check_and_push(agent_api_url=AGENT_API, subject="cust-1", expected_branch="feature/csv-export") is None


@respx.mock
def test_check_and_push_ready_pushes():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/csv-export", "changes": [], "commits": ["abc"]})
    )
    push_route = respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/csv-export", "head_sha": "abc123"})
    )
    result = check_and_push(agent_api_url=AGENT_API, subject="cust-1", expected_branch="feature/csv-export")
    assert result["branch"] == "feature/csv-export"
    assert push_route.called


@respx.mock
def test_check_and_push_raises_on_push_failure():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/x", "changes": [], "commits": ["a"]})
    )
    respx.post(f"{AGENT_API}/api/workspace/push").mock(return_value=httpx.Response(502, json={"detail": "diverged"}))
    with pytest.raises(PushError):
        check_and_push(agent_api_url=AGENT_API, subject="cust-1", expected_branch="feature/x")


# ── signoff identity (identity.principal) — never a literal, only from the caller's settings ────

@respx.mock
def test_submit_implementation_sends_principal_when_signoff_configured():
    route = respx.post(f"{AGENT_API}/invocations").mock(
        return_value=httpx.Response(202, json={"workload_id": "agent-123"})
    )
    submit_implementation(
        agent_api_url=AGENT_API, subject="cust-1", title="X", body="y",
        signoff_name="Julian Sanker", signoff_email="julian@sankergroup.org",
    )
    body = json.loads(route.calls[0].request.content)
    assert body["identity"]["principal"] == {"name": "Julian Sanker", "email": "julian@sankergroup.org"}


@respx.mock
def test_submit_implementation_omits_principal_when_only_one_of_name_email_set():
    """Both or neither — a half-configured identity is treated as not configured at all, never a
    partial one sent to the platform."""
    route = respx.post(f"{AGENT_API}/invocations").mock(
        return_value=httpx.Response(202, json={"workload_id": "agent-123"})
    )
    submit_implementation(agent_api_url=AGENT_API, subject="cust-1", title="X", body="y", signoff_name="Julian")
    body = json.loads(route.calls[0].request.content)
    assert "principal" not in body["identity"]


# ── unit_id threading (isolated per-turn worktrees) ──────────────────────────────────────────────

@respx.mock
def test_git_state_forwards_unit_as_a_query_param():
    route = respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    git_state(agent_api_url=AGENT_API, subject="cust-1", unit_id="unit-1", timeout=5.0)
    assert route.calls[0].request.url.params["unit"] == "unit-1"


@respx.mock
def test_git_state_sends_no_unit_param_when_not_given():
    route = respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    git_state(agent_api_url=AGENT_API, subject="cust-1", timeout=5.0)
    assert "unit" not in route.calls[0].request.url.params


@respx.mock
def test_push_if_ready_forwards_unit_and_expected_signoff():
    route = respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/x", "head_sha": "abc"})
    )
    push_if_ready(
        {"branch": "feature/x", "changes": []}, agent_api_url=AGENT_API, subject="cust-1",
        expected_branch="feature/x", unit_id="unit-1", expected_signoff="Julian Sanker <julian@sankergroup.org>",
    )
    body = json.loads(route.calls[0].request.content)
    assert body == {"unit": "unit-1", "expected_signoff": "Julian Sanker <julian@sankergroup.org>"}


@respx.mock
def test_push_if_ready_sends_empty_body_when_nothing_configured():
    route = respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/x", "head_sha": "abc"})
    )
    push_if_ready({"branch": "feature/x", "changes": []}, agent_api_url=AGENT_API, subject="cust-1",
                  expected_branch="feature/x")
    assert json.loads(route.calls[0].request.content) == {}


# ── open_pull_request ─────────────────────────────────────────────────────────────────────────────

@respx.mock
def test_open_pull_request_posts_title_body_base_and_unit():
    route = respx.post(f"{AGENT_API}/api/workspace/pull-request").mock(
        return_value=httpx.Response(200, json={"url": "https://github.com/x/y/pull/7", "number": 7})
    )
    result = open_pull_request(
        agent_api_url=AGENT_API, subject="cust-1", title="CSV export", body="wants it",
        base="main", unit_id="unit-1",
    )
    assert result == {"url": "https://github.com/x/y/pull/7", "number": 7}
    body = json.loads(route.calls[0].request.content)
    assert body == {"title": "CSV export", "body": "wants it", "base": "main", "unit": "unit-1"}


@respx.mock
def test_open_pull_request_raises_on_failure():
    respx.post(f"{AGENT_API}/api/workspace/pull-request").mock(return_value=httpx.Response(502))
    with pytest.raises(PullRequestError):
        open_pull_request(agent_api_url=AGENT_API, subject="cust-1", title="x", body="y", base="main")


@respx.mock
def test_two_dispatches_of_the_same_title_get_distinct_branches():
    respx.post(f"{AGENT_API}/invocations").mock(return_value=httpx.Response(202, json={"workload_id": "w"}))
    a = submit_implementation(agent_api_url=AGENT_API, subject="cust-1", title="CSV export", body="x")
    b = submit_implementation(agent_api_url=AGENT_API, subject="cust-1", title="CSV export", body="x")
    assert a["branch"] != b["branch"]
