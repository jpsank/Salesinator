"""Takes an approved feature request and turns it into a real, pushed GitHub branch.

Three steps, each its own function below:
1. `submit_implementation` — tell the AI agent to start building it (fire-and-forget; it runs in the
   background).
2. `check_and_push` — check in later: is it done? If yes, push the branch to GitHub.

Everything here runs as one dedicated Vexa account that's set up to always work in the real product
codebase (see settings.py's `product_repo_user_id`). The agent itself is never given the ability to
push to GitHub directly — it can only save its work locally. This code is the only thing that decides
when a finished piece of work actually gets pushed out.
"""

from __future__ import annotations

import logging
import re

import httpx

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
    *, agent_api_url: str, user_id: str, title: str, body: str, timeout: float = 10.0,
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
    try:
        resp = httpx.post(
            f"{agent_api_url.rstrip('/')}/invocations",
            json={
                "identity": {"subject": user_id, "launcher": "sales-cycle:feature-request"},
                "runner": "claude-code",
                "trigger": "scheduled",
                "start": {"entrypoint": {"inline": prompt}},
            },
            headers={"X-User-Id": user_id, "Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise DispatchError(f"POST /invocations failed: {type(e).__name__}: {e}") from e
    data = resp.json()
    return {"workload_id": data.get("workload_id"), "branch": branch}


def _git_state(*, agent_api_url: str, user_id: str, timeout: float) -> dict:
    try:
        resp = httpx.get(
            f"{agent_api_url.rstrip('/')}/api/workspace/git",
            headers={"X-User-Id": user_id}, timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise DispatchError(f"GET /api/workspace/git failed: {type(e).__name__}: {e}") from e
    return resp.json()


def check_and_push(
    *, agent_api_url: str, user_id: str, expected_branch: str, timeout: float = 15.0,
) -> dict | None:
    """Checks whether the agent finished: is it on the right branch, with everything saved (nothing
    left half-done)? If not, returns nothing — we'll just check again next time. If it IS done, pushes
    the branch to GitHub and returns the result. Only ever pushes the exact branch this request asked
    for, never anything else it might happen to be sitting on."""
    state = _git_state(agent_api_url=agent_api_url, user_id=user_id, timeout=timeout)
    if state.get("branch") != expected_branch:
        return None
    if state.get("changes"):  # still has unsaved work — not finished yet
        return None
    try:
        resp = httpx.post(
            f"{agent_api_url.rstrip('/')}/api/workspace/push",
            headers={"X-User-Id": user_id, "Content-Type": "application/json"},
            json={}, timeout=timeout,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise PushError(f"POST /api/workspace/push failed: {type(e).__name__}: {e}") from e
    return resp.json()
