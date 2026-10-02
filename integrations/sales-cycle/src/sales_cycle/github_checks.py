"""What did CI say about the agent's pull request?

The agent's output is only as good as its model, so it is checked by the repository's own CI (typecheck, tests, gates) on the pull request —
the verdict is read from GitHub and said once in the card's Slack thread, so a human sees "the agent's branch fails typecheck" before reading a
line of the diff. The decision (`verdict`) is pure and tested directly; `fetch_check_runs` is the one network call.

Public repositories need no token (GitHub's unauthenticated limit is about 60 calls an hour, so the sweep polls every few minutes). A repository
that cannot be read without a token is reported once as unreadable rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sales_cycle._http import call

_PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)/?$")

# A run that ended in one of these means the pull request does not pass. `cancelled` (a superseded run), `skipped`, `neutral` and `stale` do not.
_FAILED = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

NONE_AFTER_S = 8 * 60          # no check run this long after the pull request opened: nothing is running CI
GIVE_UP_AFTER_S = 60 * 60      # checks still running after this long: stop waiting


class GitHubError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PullRef:
    owner: str
    repo: str
    number: int

    @property
    def checks_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}/checks"


def parse_pr_url(url: str) -> PullRef | None:
    m = _PR_URL.match((url or "").strip())
    return PullRef(m.group(1), m.group(2), int(m.group(3))) if m else None


def verdict(runs: list[dict], *, age_s: float, none_after_s: float = NONE_AFTER_S, give_up_after_s: float = GIVE_UP_AFTER_S, checks_url: str = "") -> tuple[str, str] | None:
    """The final word on a pull request's checks, or None to keep waiting. ``runs`` are GitHub check runs (name, status, conclusion);
    ``age_s`` is how long ago the pull request opened. Returns (state, text) with state one of passed, failed, none, timeout."""
    link = f" {checks_url}" if checks_url else ""
    if not runs:
        if age_s >= none_after_s:
            return "none", (":information_source: No CI has run for this pull request, so nothing has checked the agent's code yet. If you expect "
                            "CI here, check that GitHub Actions is enabled for the repository." + link)
        return None
    if any(r.get("status") != "completed" for r in runs):
        if age_s >= give_up_after_s:
            return "timeout", f":hourglass: CI was still running an hour after the pull request opened — not waiting any longer.{link}"
        return None
    failed = sorted({r.get("name") or "a check" for r in runs if r.get("conclusion") in _FAILED})
    if failed:
        shown = ", ".join(f"`{n}`" for n in failed[:6]) + (f" and {len(failed) - 6} more" if len(failed) > 6 else "")
        return "failed", f":x: CI failed on the agent's pull request: {shown}. Review the diff with care.{link}"
    ok = sum(1 for r in runs if r.get("conclusion") == "success")
    return "passed", f":white_check_mark: CI passed on the agent's pull request ({ok} check{'s' if ok != 1 else ''}).{link}"


def fetch_head_sha(ref: PullRef, *, timeout: float = 10.0, base: str = "https://api.github.com") -> str:
    resp = _get(f"{base}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}", timeout)
    sha = (resp.json().get("head") or {}).get("sha")
    if not sha:
        raise GitHubError("the pull request has no head commit")
    return sha


def fetch_check_runs(ref: PullRef, sha: str, *, timeout: float = 10.0, base: str = "https://api.github.com") -> list[dict]:
    resp = _get(f"{base}/repos/{ref.owner}/{ref.repo}/commits/{sha}/check-runs?per_page=100", timeout)
    return [{"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion")} for r in resp.json().get("check_runs") or []]


def _get(url: str, timeout: float):
    try:
        return call("GET", url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                    timeout=timeout, error_cls=GitHubError, error_prefix="GitHub API")
    except GitHubError as e:
        m = re.search(r"\b(\d{3})\b", str(e))
        raise GitHubError(str(e), status=int(m.group(1)) if m else None) from e
