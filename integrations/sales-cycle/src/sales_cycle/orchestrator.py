"""Approved feature-request → a real branch, implemented, pushed to GitHub.

Talks to agent-api's INTERNAL port directly (X-User-Id), not the gateway — see settings.py's
`agent_api_internal_url` docstring for why. Uses the dedicated "product-repo" service account's own
identity throughout (dispatch, git-status read, push) — no shared-workspace membership grant needed;
its own primary workspace IS the product repo (swapped in once, out of band).

This deliberately does NOT expose a raw "push" tool to the agent turn (rejected in the plan) — the turn
only ever commits locally; this module decides, server-side, whether/when a finished turn's branch
actually gets pushed to GitHub.
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
    """Fires the one-shot implementation turn. Fire-and-forget — the dispatch runs asynchronously in
    its own worker container; `check_and_push` polls for its result on a later sweep."""
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
    """Returns the push result once the turn has landed on `expected_branch` with a clean tree (nothing
    left uncommitted), else None — the turn isn't finished yet, retry on the next sweep. Never pushes a
    branch other than the one this feature request asked for (a stale/wrong checkout is a no-op here,
    not a push of the wrong thing)."""
    state = _git_state(agent_api_url=agent_api_url, user_id=user_id, timeout=timeout)
    if state.get("branch") != expected_branch:
        return None
    if state.get("changes"):  # uncommitted changes still pending — the turn hasn't finished committing
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
