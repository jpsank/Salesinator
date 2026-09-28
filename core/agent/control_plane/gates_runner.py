"""gates_runner.py — the compliance checks run before pushing an isolated worktree's branch
(``ws_push`` in api.py, gated on the caller passing ``unit``). Fully generic: neither function here
knows anything about a specific product repo, org, or tooling.

Two independent checks:
  - ``check_commit_compliance`` — attribution: HEAD must carry the expected Signed-off-by line (when
    one was configured by the caller) and must never carry a Co-Authored-By: Claude trailer.
  - ``run_configured_pre_push_hook`` — respects whatever pre-push hook the ATTACHED repo already has
    configured (``core.hooksPath`` / the default ``.git/hooks/pre-push``), the same standard git
    mechanism a human's local ``git push`` would trigger — never a hardcoded call to any specific
    repo's own tooling.

Both report loudly (a list of failure strings) rather than silently rewriting anything — a violation
refuses the push with the exact problem, never an edited commit.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from shared.gitenv import scrubbed_git_env


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=scrubbed_git_env(),
    )


def check_commit_compliance(ws: Path, *, expected_signoff: str | None) -> list[str]:
    """``expected_signoff`` is a preformatted ``Name <email>`` string, or None to skip that check
    entirely (a generic deployment with no signoff identity configured pays nothing here)."""
    log = _git(ws, "log", "-1", "--format=%B", "HEAD")
    if log.returncode != 0:
        return [f"could not read HEAD commit message: {(log.stderr or '').strip()}"]
    message = log.stdout

    failures: list[str] = []
    if expected_signoff and f"Signed-off-by: {expected_signoff}" not in message:
        failures.append(f"HEAD commit is missing the required signoff line: Signed-off-by: {expected_signoff}")
    if "co-authored-by: claude" in message.lower():
        failures.append("HEAD commit carries a Co-Authored-By: Claude trailer, which is not allowed here")
    return failures


def run_configured_pre_push_hook(ws: Path) -> list[str]:
    """Resolves the effective pre-push hook the SAME way ``git push`` itself would (``core.hooksPath``,
    falling back to the default ``.git/hooks/pre-push``), and runs it if present + executable. No hook
    configured at all is a PASS, not a skip-with-a-warning — there is simply nothing to check."""
    hooks_path_result = _git(ws, "config", "--get", "core.hooksPath")
    if hooks_path_result.returncode == 0 and hooks_path_result.stdout.strip():
        configured = hooks_path_result.stdout.strip()
        hooks_dir = Path(configured) if os.path.isabs(configured) else (ws / configured)
    else:
        default = _git(ws, "rev-parse", "--git-path", "hooks")
        if default.returncode != 0:
            return []  # not a git repo at all — nothing to run
        hooks_dir = ws / default.stdout.strip()

    hook = hooks_dir / "pre-push"
    if not hook.exists() or not os.access(hook, os.X_OK):
        return []

    result = subprocess.run(
        [str(hook)], cwd=str(ws), capture_output=True, text=True, env=scrubbed_git_env(),
    )
    if result.returncode != 0:
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        return [f"pre-push hook failed (exit {result.returncode}): {output[-4000:]}"]
    return []
