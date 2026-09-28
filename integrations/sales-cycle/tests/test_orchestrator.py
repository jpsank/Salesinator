import httpx
import pytest
import respx

from sales_cycle.orchestrator import (
    DispatchError, PushError, branch_for, check_and_push, slug_for, submit_implementation,
)

AGENT_API = "http://agent-api:8100"


@pytest.mark.parametrize("title,expected", [
    ("CSV Export for Reports", "csv-export-for-reports"),
    ("SSO / SAML support!!", "sso-saml-support"),
    ("!!!", "feature"),
])
def test_slug_for(title, expected):
    assert slug_for(title) == expected


def test_branch_for_has_feature_prefix():
    assert branch_for("CSV export") == "feature/csv-export"


@respx.mock
def test_submit_implementation_posts_invocation_and_returns_branch():
    route = respx.post(f"{AGENT_API}/invocations").mock(
        return_value=httpx.Response(202, json={"workload_id": "agent-123"})
    )
    result = submit_implementation(
        agent_api_url=AGENT_API, user_id="cust-1", title="CSV export", body="wants it",
    )
    assert result == {"workload_id": "agent-123", "branch": "feature/csv-export"}
    sent = route.calls[0].request
    assert sent.headers["X-User-Id"] == "cust-1"
    import json
    body = json.loads(sent.content)
    assert body["identity"]["subject"] == "cust-1"
    assert "git checkout -b feature/csv-export" in body["start"]["entrypoint"]["inline"]


@respx.mock
def test_submit_implementation_raises_on_failure():
    respx.post(f"{AGENT_API}/invocations").mock(return_value=httpx.Response(500))
    with pytest.raises(DispatchError):
        submit_implementation(agent_api_url=AGENT_API, user_id="cust-1", title="X", body="y")


@respx.mock
def test_check_and_push_not_ready_wrong_branch():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "main", "changes": [], "commits": []})
    )
    assert check_and_push(agent_api_url=AGENT_API, user_id="cust-1", expected_branch="feature/csv-export") is None


@respx.mock
def test_check_and_push_not_ready_dirty_tree():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/csv-export", "changes": ["a.py"], "commits": []})
    )
    assert check_and_push(agent_api_url=AGENT_API, user_id="cust-1", expected_branch="feature/csv-export") is None


@respx.mock
def test_check_and_push_ready_pushes():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/csv-export", "changes": [], "commits": ["abc"]})
    )
    push_route = respx.post(f"{AGENT_API}/api/workspace/push").mock(
        return_value=httpx.Response(200, json={"remote": "vexa-sync", "url": "https://github.com/x/y",
                                                "branch": "feature/csv-export", "head_sha": "abc123"})
    )
    result = check_and_push(agent_api_url=AGENT_API, user_id="cust-1", expected_branch="feature/csv-export")
    assert result["branch"] == "feature/csv-export"
    assert push_route.called


@respx.mock
def test_check_and_push_raises_on_push_failure():
    respx.get(f"{AGENT_API}/api/workspace/git").mock(
        return_value=httpx.Response(200, json={"branch": "feature/x", "changes": [], "commits": ["a"]})
    )
    respx.post(f"{AGENT_API}/api/workspace/push").mock(return_value=httpx.Response(502, json={"detail": "diverged"}))
    with pytest.raises(PushError):
        check_and_push(agent_api_url=AGENT_API, user_id="cust-1", expected_branch="feature/x")
