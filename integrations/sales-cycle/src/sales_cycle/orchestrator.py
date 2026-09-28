"""Takes an approved feature request and turns it into a real, pushed GitHub branch.

Three steps, each its own function below:
1. `submit_implementation` — tell the AI agent to start building it (fire-and-forget; it runs in the
   background).
2. `check_and_push` — check in later: is it done? If yes, push the branch to GitHub.

Everything here runs as one dedicated Vexa subject whose own workspace is set up to always be the
real product codebase (see settings.py's `product_repo_subject`). The agent itself is never given
the ability to push to GitHub directly — it can only save its work locally. This code is the only
thing that decides when a finished piece of work actually gets pushed out.
"""

from __future__ import annotations

import logging
import re

from sales_cycle._http import call

logger = logging.getLogger("sales_cycle.orchestrator")


class DispatchError(RuntimeError):
    pass


class PushError(RuntimeError):
    pass


def slug_for(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s or "feature"


def branch_for(title: str) -> str:
    return f"feature/{slug_for(title)}"


def submit_implementation(
    *, agent_api_url: str, subject: str, title: str, body: str, timeout: float = 10.0,
) -> dict:
    """Starts the AI agent working on this request. We don't wait around for it to finish — it runs
    in the background, and `check_and_push` (below) checks on it later."""
    branch = branch_for(title)
    prompt = (
        f"A customer explicitly asked for this product capability:\n\n"
        f"Request: {title}\n"
        f"Their own words: {body}\n\n"
        f"Working in this repository: first run `git checkout -b {branch}` to create the branch. "
        f"Then implement a minimal, working version of the capability, with tests if the repo has a "
        f"test suite. Commit your changes with a clear message. Do NOT push — that is handled "
        f"separately, outside this turn."
    )
    resp = call(
        "POST", f"{agent_api_url.rstrip('/')}/invocations",
        json={
            "identity": {"subject": subject, "launcher": "sales-cycle:feature-request"},
            "runner": "claude-code",
            "trigger": "scheduled",
            "start": {"entrypoint": {"inline": prompt}},
        },
        headers={"X-User-Id": subject, "Content-Type": "application/json"}, timeout=timeout,
        error_cls=DispatchError, error_prefix="POST /invocations",
    )
    data = resp.json()
    return {"workload_id": data.get("workload_id"), "branch": branch}


def git_state(*, agent_api_url: str, subject: str, timeout: float) -> dict:
    resp = call(
        "GET", f"{agent_api_url.rstrip('/')}/api/workspace/git",
        headers={"X-User-Id": subject}, timeout=timeout,
        error_cls=DispatchError, error_prefix="GET /api/workspace/git",
    )
    return resp.json()


def push_if_ready(
    state: dict, *, agent_api_url: str, subject: str, expected_branch: str, timeout: float = 15.0,
) -> dict | None:
    """Given an already-fetched git state (see `git_state`): is the agent on the right branch, with
    everything saved (nothing left half-done)? If not, returns nothing — we'll just check again next
    time. If it IS done, pushes the branch to GitHub and returns the result. Only ever pushes the
    exact branch this request asked for, never anything else it might happen to be sitting on.

    Takes `state` rather than fetching it itself so a caller checking several approvals against the
    SAME subject's workspace in one sweep (`process_approved`) can fetch it once and reuse it —
    every approval for one deployment shares the one `product_repo_subject` workspace, so their git
    state is identical within a sweep."""
    if state.get("branch") != expected_branch:
        return None
    if state.get("changes"):  # still has unsaved work — not finished yet
        return None
    resp = call(
        "POST", f"{agent_api_url.rstrip('/')}/api/workspace/push",
        headers={"X-User-Id": subject, "Content-Type": "application/json"}, json={}, timeout=timeout,
        error_cls=PushError, error_prefix="POST /api/workspace/push",
    )
    return resp.json()


def check_and_push(
    *, agent_api_url: str, subject: str, expected_branch: str, timeout: float = 15.0,
) -> dict | None:
    """Single-approval convenience wrapper around `git_state` + `push_if_ready` — fetches state
    itself. A caller checking several approvals in one sweep should fetch state once and call
    `push_if_ready` directly instead (see `process_approved` in api.py)."""
    state = git_state(agent_api_url=agent_api_url, subject=subject, timeout=timeout)
    return push_if_ready(
        state, agent_api_url=agent_api_url, subject=subject, expected_branch=expected_branch, timeout=timeout,
    )
