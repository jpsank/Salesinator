"""Takes an approved feature request and turns it into a real, pushed GitHub branch with an open
pull request.

Three steps, each its own function below:
1. `submit_implementation` — tell the AI agent to start building it (fire-and-forget; it runs in the
   background).
2. `push_if_ready` — check in later: is it done? If yes, push the branch to GitHub.
3. `open_pull_request` — once pushed, open a PR for it (also what most preview-hosting platforms
   need to trigger a build — see README.md).

Everything here runs as one dedicated Vexa subject whose own workspace is set up to always be the
real product codebase (see settings.py's `product_repo_subject`). The agent itself is never given
the ability to push to GitHub directly — it can only save its work locally. This code is the only
thing that decides when a finished piece of work actually gets pushed out.

Every dispatch requests core/agent's per-turn worktree isolation (`isolation.mode: "worktree"`) —
without it, two feature requests approved close together would run their `git checkout -b`/`git
commit` against the SAME shared directory and corrupt each other. When `settings.py`'s
`product_repo_signoff_name`/`_email` are configured, every commit an automated turn makes also
carries a proper Signed-off-by line (core/agent's own signoff-hook mechanism) instead of the
subject's generic placeholder identity — see CONTRIBUTOR_RIGHTS.md for why that matters when the
product repo IS this same repo (dogfooding)."""

from __future__ import annotations

import logging
import re

from sales_cycle._http import call

logger = logging.getLogger("sales_cycle.orchestrator")


class DispatchError(RuntimeError):
    pass


class PushError(RuntimeError):
    pass


class PullRequestError(RuntimeError):
    pass


def slug_for(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s or "feature"


def branch_for(title: str) -> str:
    return f"feature/{slug_for(title)}"


def submit_implementation(
    *, agent_api_url: str, subject: str, title: str, body: str,
    signoff_name: str = "", signoff_email: str = "", timeout: float = 10.0,
) -> dict:
    """Starts the AI agent working on this request. We don't wait around for it to finish — it runs
    in the background, and `push_if_ready` (below) checks on it later.

    `signoff_name`/`signoff_email`, when BOTH given, ride as `identity.principal` — core/agent's
    existing internal-attribution field (unrelated to the unit.v1 wire schema; stripped before the
    contract check) that its own worktree-provisioning step uses to configure git identity + install
    the signoff hook. Neither is a literal here — both come from the caller's own settings."""
    branch = branch_for(title)
    prompt = (
        f"A customer explicitly asked for this product capability:\n\n"
        f"Request: {title}\n"
        f"Their own words: {body}\n\n"
        f"Working in this repository: first run `git checkout -b {branch}` to create the branch. "
        f"Then implement a minimal, working version of the capability, with tests if the repo has a "
        f"test suite. Commit your changes with a clear message. Do not add a `Co-Authored-By` "
        f"trailer to your commit message. Do NOT push — that is handled separately, outside this turn."
    )
    identity: dict = {"subject": subject, "launcher": "sales-cycle:feature-request"}
    if signoff_name and signoff_email:
        identity["principal"] = {"name": signoff_name, "email": signoff_email}
    resp = call(
        "POST", f"{agent_api_url.rstrip('/')}/invocations",
        json={
            "identity": identity,
            "runner": "claude-code",
            "workspaces": [{"id": subject, "mode": "rw"}],
            "trigger": "scheduled",
            "start": {"entrypoint": {"inline": prompt}},
            "isolation": {"mode": "worktree"},
        },
        headers={"X-User-Id": subject, "Content-Type": "application/json"}, timeout=timeout,
        error_cls=DispatchError, error_prefix="POST /invocations",
    )
    data = resp.json()
    return {"workload_id": data.get("workload_id"), "branch": branch}


def git_state(*, agent_api_url: str, subject: str, unit_id: str | None = None, timeout: float) -> dict:
    """`unit_id` (the workload_id `submit_implementation` returned) addresses that turn's OWN
    isolated worktree instead of the subject's plain shared workspace — required whenever isolation
    was requested at dispatch time, since that's the only place the turn's work actually landed."""
    resp = call(
        "GET", f"{agent_api_url.rstrip('/')}/api/workspace/git",
        headers={"X-User-Id": subject}, params={"unit": unit_id} if unit_id else None, timeout=timeout,
        error_cls=DispatchError, error_prefix="GET /api/workspace/git",
    )
    return resp.json()


def push_if_ready(
    state: dict, *, agent_api_url: str, subject: str, expected_branch: str,
    unit_id: str | None = None, expected_signoff: str | None = None, timeout: float = 15.0,
) -> dict | None:
    """Given an already-fetched git state (see `git_state`): is the agent on the right branch, with
    everything saved (nothing left half-done)? If not, returns nothing — we'll just check again next
    time. If it IS done, pushes the branch to GitHub and returns the result. Only ever pushes the
    exact branch this request asked for, never anything else it might happen to be sitting on.

    Takes `state` rather than fetching it itself so a caller checking several approvals against the
    SAME subject's workspace in one sweep (`process_approved`) can fetch it once and reuse it —
    every approval for one deployment shares the one `product_repo_subject` workspace, so their git
    state is identical within a sweep. `unit_id`/`expected_signoff` are forwarded to the same
    isolated worktree `git_state` read from, and to the (optional) signoff-attribution check
    core/agent runs before allowing the push."""
    if state.get("branch") != expected_branch:
        return None
    if state.get("changes"):  # still has unsaved work — not finished yet
        return None
    push_body: dict = {}
    if unit_id:
        push_body["unit"] = unit_id
    if expected_signoff:
        push_body["expected_signoff"] = expected_signoff
    resp = call(
        "POST", f"{agent_api_url.rstrip('/')}/api/workspace/push",
        headers={"X-User-Id": subject, "Content-Type": "application/json"}, json=push_body, timeout=timeout,
        error_cls=PushError, error_prefix="POST /api/workspace/push",
    )
    return resp.json()


def check_and_push(
    *, agent_api_url: str, subject: str, expected_branch: str,
    unit_id: str | None = None, expected_signoff: str | None = None, timeout: float = 15.0,
) -> dict | None:
    """Single-approval convenience wrapper around `git_state` + `push_if_ready` — fetches state
    itself. A caller checking several approvals in one sweep should fetch state once and call
    `push_if_ready` directly instead (see `process_approved` in api.py)."""
    state = git_state(agent_api_url=agent_api_url, subject=subject, unit_id=unit_id, timeout=timeout)
    return push_if_ready(
        state, agent_api_url=agent_api_url, subject=subject, expected_branch=expected_branch,
        unit_id=unit_id, expected_signoff=expected_signoff, timeout=timeout,
    )


def open_pull_request(
    *, agent_api_url: str, subject: str, title: str, body: str, base: str,
    unit_id: str | None = None, timeout: float = 15.0,
) -> dict:
    """Opens a pull request for the ALREADY-PUSHED branch this request landed on — most
    preview-hosting platforms (Vercel, Netlify, …) only build previews for pull requests, not bare
    pushed branches, so this is also what makes the "live preview" half of the pipeline fire, not
    just a courtesy for human review. `unit_id` addresses the same isolated worktree the push used —
    core/agent resolves the PR's head branch + target repo from it, same as for the push itself."""
    pr_body: dict = {"title": title, "body": body, "base": base}
    if unit_id:
        pr_body["unit"] = unit_id
    resp = call(
        "POST", f"{agent_api_url.rstrip('/')}/api/workspace/pull-request",
        headers={"X-User-Id": subject, "Content-Type": "application/json"}, json=pr_body, timeout=timeout,
        error_cls=PullRequestError, error_prefix="POST /api/workspace/pull-request",
    )
    return resp.json()
