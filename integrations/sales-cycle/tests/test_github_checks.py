"""Reading CI's verdict on the agent's pull request: when to keep waiting, and what to say once the checks are in."""
import httpx
import pytest
import respx

from sales_cycle.github_checks import (
    GitHubError, PullRef, DEFAULT_IGNORED_CHECKS, fetch_changed_files, fetch_check_runs, fetch_head_sha, fetch_workspace_globs, ignored_checks, parse_pr_url,
    uncovered_by_ci, verdict, workspace_globs,
)


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
    assert state == "none" and "No CI check has run" in text and "Actions" in text


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


# ── what CI covers: the pnpm workspace ──

REAL_WORKSPACE = """# Workspace packages.
packages:
  - "core/runtime"
  - "core/meetings/services/*"
  - "clients/terminal"
# Build scripts pnpm may run
allowBuilds:
  esbuild: true
"""


def test_the_workspace_globs_are_read_from_pnpm_workspace_yaml():
    assert workspace_globs(REAL_WORKSPACE) == ["core/runtime", "core/meetings/services/*", "clients/terminal"]
    assert workspace_globs("packages:\n  - 'a/*'\n  - b\n") == ["a/*", "b"]
    assert workspace_globs("allowBuilds:\n  - nope\n") == [] and workspace_globs("") == []


def test_only_typescript_and_javascript_outside_the_workspace_are_reported():
    globs = workspace_globs(REAL_WORKSPACE)
    got = uncovered_by_ci([
        "core/runtime/src/a.ts", "core/meetings/services/bot/src/b.ts", "clients/terminal/src/c.tsx",          # covered
        "packages/transcript-rendering/src/manager.ts", "packages/transcript-rendering/src/index.ts", "scripts/x.mjs",   # not
        "README.md", "core/x/y.py", "packages/x/package.json", "core/meetings/services/bot/node_modules/dep/i.js"], globs)
    assert got == ["packages/transcript-rendering/src/index.ts", "packages/transcript-rendering/src/manager.ts", "scripts/x.mjs"]


def test_a_glob_star_is_one_directory_level_and_double_star_any_depth():
    assert uncovered_by_ci(["a/b/c/x.ts"], ["a/*"]) == []              # inside package a/b
    assert uncovered_by_ci(["a/x.ts"], ["a/*"]) == ["a/x.ts"]          # directly in a/, not in a package under it
    assert uncovered_by_ci(["a/b/c/d/x.ts"], ["a/**"]) == []


def test_with_no_workspace_nothing_is_claimed():
    assert uncovered_by_ci(["packages/x/a.ts"], []) == []


def test_the_verdict_names_what_was_not_covered_and_truncates():
    runs = [_run("node")]
    text = verdict(runs, age_s=1, uncovered=["p/a.ts"])[1]
    assert "CI passed" in text and "`p/a.ts`" in text and "review them by hand" in text
    assert "and 3 more" in verdict(runs, age_s=1, uncovered=[f"p/{i}.ts" for i in range(8)])[1]
    assert "warning" not in verdict(runs, age_s=1, uncovered=[])[1]
    assert "warning" not in verdict(runs, age_s=1)[1]


@respx.mock
def test_changed_files_are_paged():
    ref = PullRef("o", "r", 2)
    page1 = [{"filename": f"f{i}.ts"} for i in range(100)]
    respx.get("https://api.github.com/repos/o/r/pulls/2/files", params={"page": "1"}).mock(return_value=httpx.Response(200, json=page1))
    respx.get("https://api.github.com/repos/o/r/pulls/2/files", params={"page": "2"}).mock(return_value=httpx.Response(200, json=[{"filename": "last.ts"}]))
    got = fetch_changed_files(ref)
    assert len(got) == 101 and got[-1] == "last.ts"


@respx.mock
def test_the_workspace_file_is_read_at_the_commit_and_a_missing_one_is_empty():
    ref = PullRef("o", "r", 2)
    respx.get("https://raw.githubusercontent.com/o/r/abc/pnpm-workspace.yaml").mock(return_value=httpx.Response(200, text=REAL_WORKSPACE))
    respx.get("https://raw.githubusercontent.com/o/r/def/pnpm-workspace.yaml").mock(return_value=httpx.Response(404))
    assert fetch_workspace_globs(ref, "abc")[0] == "core/runtime"
    assert fetch_workspace_globs(ref, "def") == []


# ── process checks are not code quality ──

IGNORE = ignored_checks(DEFAULT_IGNORED_CHECKS)


def test_upstream_process_checks_do_not_fail_the_verdict():
    runs = [_run("gates"), _run("node"), _run("merge-card", conclusion="failure"), _run("contribution-rights", conclusion="failure")]
    state, text = verdict(runs, age_s=1, ignore=IGNORE)
    assert state == "passed" and "2 checks" in text                      # only gates and node count
    assert verdict(runs, age_s=1)[0] == "failed"                         # without the ignore list the paperwork fails it


def test_a_real_failure_is_still_reported_alongside_ignored_ones():
    state, text = verdict([_run("node", conclusion="failure"), _run("merge-card", conclusion="failure")], age_s=1, ignore=IGNORE)
    assert state == "failed" and "`node`" in text and "merge-card" not in text


def test_only_ignored_checks_means_no_code_check_ran():
    assert verdict([_run("merge-card")], age_s=60, ignore=IGNORE) is None
    state, text = verdict([_run("merge-card", conclusion="failure")], age_s=9 * 60, ignore=IGNORE)
    assert state == "none" and "No CI check has run" in text


def test_the_ignore_setting_is_a_comma_separated_list():
    assert ignored_checks(" a, b ,,c") == {"a", "b", "c"} and ignored_checks("") == frozenset()
    assert {"merge-card", "pr-value", "contribution-rights"} <= IGNORE
