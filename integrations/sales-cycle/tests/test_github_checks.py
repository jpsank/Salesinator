"""Reading CI's verdict on the agent's pull request: when to keep waiting, and what to say once the checks are in."""
import httpx
import pytest
import respx

from sales_cycle.github_checks import GitHubError, PullRef, fetch_check_runs, fetch_head_sha, parse_pr_url, verdict


def _run(name, status="completed", conclusion="success"):
    return {"name": name, "status": status, "conclusion": conclusion}


def test_pull_request_urls_are_parsed_and_others_refused():
    assert parse_pr_url("https://github.com/jpsank/Salesinator/pull/2") == PullRef("jpsank", "Salesinator", 2)
    assert parse_pr_url("https://github.com/o/r/pull/12/") == PullRef("o", "r", 12)
    assert PullRef("o", "r", 7).checks_url == "https://github.com/o/r/pull/7/checks"
    for bad in ("", "https://example.com/o/r/pull/1", "https://github.com/o/r/issues/1", "https://github.com/o/r/pull/x", None):
        assert parse_pr_url(bad) is None


def test_all_checks_green_passes():
    state, text = verdict([_run("static"), _run("python"), _run("node")], age_s=300, checks_url="https://x/checks")
    assert state == "passed" and "3 checks" in text and "https://x/checks" in text


def test_a_failed_check_fails_the_pull_request_and_names_it():
    state, text = verdict([_run("static"), _run("node", conclusion="failure"), _run("gates", conclusion="failure")], age_s=300)
    assert state == "failed" and "`node`" in text and "`gates`" in text and "`static`" not in text


def test_timed_out_and_action_required_count_as_failures_but_cancelled_skipped_neutral_do_not():
    assert verdict([_run("a", conclusion="timed_out")], age_s=1)[0] == "failed"
    assert verdict([_run("a", conclusion="action_required")], age_s=1)[0] == "failed"
    state, text = verdict([_run("a"), _run("b", conclusion="cancelled"), _run("c", conclusion="skipped"), _run("d", conclusion="neutral")], age_s=1)
    assert state == "passed" and "1 check)" in text


def test_it_keeps_waiting_while_any_check_is_still_running():
    assert verdict([_run("a"), _run("b", status="in_progress", conclusion=None)], age_s=600) is None
    assert verdict([_run("a", status="queued", conclusion=None)], age_s=10) is None


def test_with_no_checks_it_waits_then_says_nothing_is_running_ci():
    assert verdict([], age_s=60) is None
    state, text = verdict([], age_s=8 * 60, checks_url="https://x/checks")
    assert state == "none" and "No CI has run" in text and "Actions" in text


def test_it_gives_up_on_checks_that_never_finish():
    assert verdict([_run("a", status="in_progress", conclusion=None)], age_s=59 * 60) is None
    assert verdict([_run("a", status="in_progress", conclusion=None)], age_s=61 * 60)[0] == "timeout"


def test_many_failures_are_summarised():
    runs = [_run(f"job{i}", conclusion="failure") for i in range(9)]
    assert "and 3 more" in verdict(runs, age_s=1)[1]


@respx.mock
def test_the_head_commit_and_check_runs_are_read_from_github():
    ref = PullRef("o", "r", 2)
    respx.get("https://api.github.com/repos/o/r/pulls/2").mock(return_value=httpx.Response(200, json={"head": {"sha": "abc123"}}))
    route = respx.get("https://api.github.com/repos/o/r/commits/abc123/check-runs").mock(return_value=httpx.Response(200, json={"check_runs": [
        {"name": "static", "status": "completed", "conclusion": "success", "id": 1}, {"name": "node", "status": "queued", "conclusion": None}]}))
    assert fetch_head_sha(ref) == "abc123"
    assert fetch_check_runs(ref, "abc123") == [{"name": "static", "status": "completed", "conclusion": "success"}, {"name": "node", "status": "queued", "conclusion": None}]
    assert route.calls[0].request.url.params["per_page"] == "100"


@respx.mock
def test_an_unreadable_repository_is_a_typed_error_with_its_status():
    respx.get("https://api.github.com/repos/o/r/pulls/2").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(GitHubError) as e:
        fetch_head_sha(PullRef("o", "r", 2))
    assert e.value.status == 404
