"""When the agent has finished but its branch cannot be pushed (or its pull request opened), the sweep used to retry forever and quietly —
the only trace was a stack trace in a log. The first time it fails for a given reason it now says so in the card's Slack thread, with what
to do for the common cause (an expired GitHub token); and when the pull request opens, the link goes in the thread."""
import json

import httpx
import pytest
import respx

import sales_cycle.api as api_module
from conftest import AGENT_API, client
from sales_cycle._http import call_detailed, error_detail
from sales_cycle.orchestrator import PushError, is_auth_failure

SLACK_POST = "https://slack.com/api/chat.postMessage"
AUTH = "git push failed: git push vexa-sync feature/x failed: remote: Invalid username or token. Password authentication is not supported for Git operations.\nfatal: Authentication failed for 'https://github.com/o/r.git/'"


@pytest.fixture(autouse=True)
def _slack_env(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SALES_CYCLE_AGENT_API_INTERNAL_URL", AGENT_API)
    monkeypatch.setenv("SALES_CYCLE_PRODUCT_REPO_SUBJECT", "product-repo")


def _dispatched():
    store = api_module.get_store()
    store.record_pending_approval(slack_channel="C1", slack_ts="1.1", workspace_id="w", source_key="k", title="CSV export", body="b")
    a = store.approve(slack_channel="C1", slack_ts="1.1")
    store.claim_for_dispatch(a.id)
    store.mark_dispatched(a.id, branch="feature/x", workload_id="unit-1")
    return store, a


def _mocks(*, push, pr=None):
    respx.get(f"{AGENT_API}/api/workspace/git").mock(return_value=httpx.Response(200, json={"branch": "feature/x", "changes": [], "commits": []}))
    pushed = respx.post(f"{AGENT_API}/api/workspace/push").mock(side_effect=push if callable(push) else None, return_value=None if callable(push) else push)
    if pr is not None:
        respx.post(f"{AGENT_API}/api/workspace/pull-request").mock(side_effect=pr if callable(pr) else None, return_value=None if callable(pr) else pr)
    return pushed, respx.post(SLACK_POST).mock(return_value=httpx.Response(200, json={"ok": True, "ts": "9.9"}))


def _replies(route):
    return [json.loads(c.request.content) for c in route.calls]


@respx.mock
def test_an_expired_github_token_is_said_once_with_how_to_fix_it():
    store, a = _dispatched()
    _, slack = _mocks(push=httpx.Response(502, json={"detail": AUTH}))
    client.post("/internal/process-approved"); client.post("/internal/process-approved"); client.post("/internal/process-approved")
    [reply] = _replies(slack)
    assert reply["thread_ts"] == "1.1" and reply["channel"] == "C1"
    assert "GitHub rejected the token" in reply["text"] and "Settings → Integrations → GitHub" in reply["text"] and "Use this repo" in reply["text"]
    assert store.list_dispatched_unpushed()[0].last_error                      # still retrying, the reason is remembered


@respx.mock
def test_another_reason_is_said_plainly_without_the_token_advice():
    _dispatched()
    _, slack = _mocks(push=httpx.Response(502, json={"detail": "git push failed: remote: Repository not found."}))
    client.post("/internal/process-approved")
    [reply] = _replies(slack)
    assert "Repository not found" in reply["text"] and "Use this repo" not in reply["text"] and "pushing its branch" in reply["text"]


@respx.mock
def test_a_changed_reason_is_said_again():
    _dispatched()
    reasons = iter([AUTH, "git push failed: remote: Repository not found."])
    _, slack = _mocks(push=lambda request: httpx.Response(502, json={"detail": next(reasons)}))
    client.post("/internal/process-approved"); client.post("/internal/process-approved")
    assert len(_replies(slack)) == 2


@respx.mock
def test_once_it_pushes_the_problem_is_cleared_and_the_pull_request_link_is_posted():
    store, a = _dispatched()
    state = {"n": 0}
    def push(request):
        state["n"] += 1
        return httpx.Response(502, json={"detail": AUTH}) if state["n"] == 1 else httpx.Response(200, json={"pushed": True})
    _, slack = _mocks(push=push, pr=httpx.Response(200, json={"url": "https://github.com/o/r/pull/7"}))
    client.post("/internal/process-approved")                 # fails: said once
    out = client.post("/internal/process-approved").json()    # pushes, then opens the pull request
    assert out["pushed"] == [a.id] and out["opened"] == [a.id]
    done = store.get_approval(a.id)
    assert done.status == "done" and done.pr_url == "https://github.com/o/r/pull/7" and done.last_error is None
    texts = [r["text"] for r in _replies(slack)]
    assert len(texts) == 2 and "GitHub rejected the token" in texts[0] and "https://github.com/o/r/pull/7" in texts[1]


@respx.mock
def test_a_pull_request_that_cannot_be_opened_is_said_once():
    store, a = _dispatched()
    _, slack = _mocks(push=httpx.Response(200, json={"pushed": True}), pr=httpx.Response(502, json={"detail": "pull request failed: Resource not accessible by personal access token"}))
    client.post("/internal/process-approved"); client.post("/internal/process-approved")
    [reply] = _replies(slack)
    assert "opening the pull request failed" in reply["text"] and "Resource not accessible" in reply["text"]
    assert store.get_approval(a.id).status == "pushed"            # branch is up; the sweep keeps trying the pull request


@respx.mock
def test_a_failing_thread_reply_never_breaks_the_sweep():
    _dispatched()
    _mocks(push=httpx.Response(502, json={"detail": AUTH}))
    respx.post(SLACK_POST).mock(return_value=httpx.Response(500))
    assert client.post("/internal/process-approved").status_code == 200


@respx.mock
def test_a_token_in_an_error_body_never_reaches_slack():
    _dispatched()
    _, slack = _mocks(push=httpx.Response(502, json={"detail": "git push failed: could not push to https://x-access-token:ghp_SECRET123@github.com/o/r.git — boom"}))
    client.post("/internal/process-approved")
    text = _replies(slack)[0]["text"]
    assert "ghp_SECRET123" not in text and "x-access-token" not in text


# ── the pieces ──

def test_auth_failures_are_told_from_other_failures():
    assert all(is_auth_failure(m) for m in (AUTH, "remote: Invalid username or token", "Bad credentials", "HTTP 403: Permission to o/r denied to u"))
    assert not any(is_auth_failure(m) for m in ("remote: Repository not found.", "HTTP 502: boom", "timed out"))


@respx.mock
def test_call_detailed_keeps_the_servers_reason_and_hides_secrets():
    respx.post("http://s/x").mock(return_value=httpx.Response(502, json={"detail": "nope ghp_ABC123 https://u:pw@h/r"}))
    with pytest.raises(PushError) as e:
        call_detailed("POST", "http://s/x", error_cls=PushError, error_prefix="POST /x", timeout=5)
    msg = str(e.value)
    assert "HTTP 502" in msg and "nope" in msg and "ghp_ABC123" not in msg and "u:pw" not in msg


def test_error_detail_falls_back_to_text_and_truncates():
    assert error_detail(httpx.Response(500, text="plain  words\nhere")) == "plain words here"
    assert len(error_detail(httpx.Response(500, json={"detail": "x" * 1000}))) == 300


@respx.mock
def test_call_detailed_wraps_a_transport_failure():
    respx.post("http://s/x").mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(PushError, match="ConnectError"):
        call_detailed("POST", "http://s/x", error_cls=PushError, error_prefix="POST /x", timeout=5)
