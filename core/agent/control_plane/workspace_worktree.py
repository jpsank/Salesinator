"""workspace_worktree.py — per-invocation git worktree isolation, opt-in via ``isolation.mode ==
"worktree"`` on a unit.v1 Invocation (see ``unit.schema.json``).

Why this exists: a subject's workspace is ONE persistent directory (``<root>/<subject>``), reused
by every dispatch for that subject. Nothing serializes concurrent turns against it — two different
approved requests dispatched close together can corrupt each other's git state (two `git checkout
-b` / `git commit` runs racing in the same working tree and index). This mirrors, for automated
turns, the same rule AGENTS.md already sets for human contributors: your own worktree before your
first edit. A `git worktree` is cheap (shares the object store with the baseline clone, no re-clone)
and gives each turn its own working tree + index, so concurrent turns simply can't collide.

Fully generic: nothing here knows about any particular product repo, org, or tooling. The optional
``principal``/``setup_cmd`` inputs are caller-supplied data, never literals.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from shared.gitenv import scrubbed_git_env, untrusted_exec_env

log = logging.getLogger(__name__)

WORKTREE_DIRNAME = ".worktrees"
# A worktree outlives its push (it is released only once the PR opens), so one whose PR never opens — a
# persistently failing PR call, an abandoned approval — would otherwise stay on disk and registered forever.
WORKTREE_MAX_AGE_SEC = 3 * 24 * 3600

# CONTRIBUTOR_RIGHTS.md's own sanctioned mechanism (verbatim) for stamping a Signed-off-by line on
# every commit made in a checkout with a configured git identity — reused here, not reinvented.
_PREPARE_COMMIT_MSG_HOOK = """#!/bin/sh
# Installed by workspace_worktree.py — appends Signed-off-by using this checkout's own git identity.
# See CONTRIBUTOR_RIGHTS.md for the sanctioned form of this hook.
name="$(git config user.name)"
email="$(git config user.email)"
if [ -n "$name" ] && [ -n "$email" ]; then
    trailer="Signed-off-by: $name <$email>"
    if ! grep -qF "$trailer" "$1"; then
        printf "\\n%s\\n" "$trailer" >> "$1"
    fi
fi
"""


class WorktreeError(RuntimeError):
    """A worktree operation failed. Deliberately NOT caught and fallen-soft by callers — silently
    falling back to the shared baseline directory would reintroduce the exact corruption bug this
    module exists to prevent."""


def _baseline_dir(root: Path, subject: str) -> Path:
    ws = (root / subject).resolve()
    if ws != root.resolve() and root.resolve() not in ws.parents:
        raise WorktreeError("invalid subject")
    return ws


def worktree_dir_for(root: str | Path, subject: str, unit_id: str) -> Path:
    """Deterministic on-disk path for one turn's isolated worktree. Lives under the already-bound
    store root, so the Runtime's existing subpath-bind logic needs no changes to reach it. ``unit_id``
    arrives from HTTP callers, so it must be ONE path component — anything else raises ``ValueError``."""
    if not unit_id or unit_id in (".", "..") or any(c in unit_id for c in "/\\\0"):
        raise ValueError("invalid unit id")
    return Path(root) / WORKTREE_DIRNAME / subject / unit_id


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=scrubbed_git_env(),
    )


def _worktree_registered(baseline: Path, dest: Path) -> bool:
    listed = _git(baseline, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        return False
    dest_s = str(dest.resolve())
    return any(line == f"worktree {dest_s}" for line in listed.stdout.splitlines())


def _ensure_identity_and_hook(baseline: Path, principal: Optional[dict]) -> None:
    """One-time, idempotent setup shared by every worktree of this subject (linked worktrees share
    ``$GIT_DIR/config`` and ``$GIT_DIR/hooks``): the caller-supplied git identity, and the
    CONTRIBUTOR_RIGHTS.md-sanctioned signoff hook — UNLESS the attached repo already configures its
    own hooks path, in which case it is left alone (never override a repo's own hook setup)."""
    name = (principal or {}).get("name")
    email = (principal or {}).get("email")
    # ``--local``: the identity must live in THIS repo's config. A bare ``--get`` also finds the host's
    # global identity, so the repo never got one — and a worker container, which has no global config, then
    # committed without any identity the signoff hook could read (so no Signed-off-by, and the push refused).
    if name and not _git(baseline, "config", "--local", "--get", "user.name").stdout.strip():
        _git(baseline, "config", "--local", "user.name", str(name))
    if email and not _git(baseline, "config", "--local", "--get", "user.email").stdout.strip():
        _git(baseline, "config", "--local", "user.email", str(email))

    hooks_path = _git(baseline, "rev-parse", "--git-path", "hooks").stdout.strip()
    if not hooks_path:
        return
    custom = _git(baseline, "config", "--get", "core.hooksPath")
    if custom.returncode == 0 and custom.stdout.strip():
        return  # the attached repo already manages its own hooks — don't touch it
    hook_file = baseline / hooks_path / "prepare-commit-msg"
    if not hook_file.exists():
        hook_file.parent.mkdir(parents=True, exist_ok=True)
        hook_file.write_text(_PREPARE_COMMIT_MSG_HOOK)
        hook_file.chmod(0o755)


def provision_worktree(
    root: str | Path, subject: str, unit_id: str, *,
    principal: Optional[dict] = None, setup_cmd: Optional[str] = None,
) -> Path:
    """Idempotent: returns the existing worktree if this unit_id was already provisioned (retry-safe),
    else creates a fresh ``git worktree add --detach`` off the subject's baseline clone HEAD. Detached
    — not `-b <branch>` — because the dispatched turn itself decides the branch name and runs its own
    `git checkout -b`.

    Raises ``WorktreeError`` loudly on failure. Never fails soft: a worktree hiccup falling back to
    the shared baseline would let two turns collide on it again, defeating the whole point."""
    rootp = Path(root)
    baseline = _baseline_dir(rootp, subject)
    if not (baseline / ".git").exists():
        raise WorktreeError(f"subject {subject!r} has no baseline workspace to branch a worktree from")
    dest = worktree_dir_for(rootp, subject, unit_id)

    if dest.exists() and _worktree_registered(baseline, dest):
        return dest  # already provisioned — a retry of the same unit_id, not a fresh one

    reap_stale_worktrees(rootp, subject)
    _ensure_identity_and_hook(baseline, principal)

    dest.parent.mkdir(parents=True, exist_ok=True)
    added = _git(baseline, "worktree", "add", "--detach", str(dest), "HEAD")
    if added.returncode != 0:
        raise WorktreeError(f"git worktree add failed: {(added.stderr or '').strip()}")

    if setup_cmd:
        # Runs the repo's own dependency lifecycle scripts, so it gets the allowlisted env — never
        # agent-api's. Registry credentials a private install needs are named explicitly by the operator.
        extra = tuple(n.strip() for n in (os.environ.get("VEXA_WORKSPACE_WORKTREE_SETUP_ENV") or "").split(",") if n.strip())
        try:
            setup = subprocess.run(
                setup_cmd, shell=True, cwd=str(dest), capture_output=True, text=True,
                env=untrusted_exec_env(*extra), timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise WorktreeError("worktree setup command timed out after 600s")
        if setup.returncode != 0:
            raise WorktreeError(
                f"worktree setup command failed (exit {setup.returncode}): {(setup.stderr or '').strip()[-2000:]}"
            )
    return dest


def release_worktree(root: str | Path, subject: str, unit_id: str) -> None:
    """Targeted removal of ONE worktree — never a blanket `git worktree prune`, which could race a
    sibling unit's in-progress `provision_worktree`. Best-effort: logs and swallows failure, since a
    cleanup hiccup must never turn an already-successful push into an error."""
    rootp = Path(root)
    try:
        baseline = _baseline_dir(rootp, subject)
        dest = worktree_dir_for(rootp, subject, unit_id)
        removed = _git(baseline, "worktree", "remove", "--force", str(dest))
        if removed.returncode != 0 and dest.exists():
            log.warning("worktree release failed for subject=%s unit=%s: %s",
                        subject, unit_id, (removed.stderr or "").strip())
    except Exception:  # noqa: BLE001 — cleanup must never raise into a caller's success path
        log.exception("worktree release raised for subject=%s unit=%s", subject, unit_id)


def reap_stale_worktrees(root: str | Path, subject: str, *, max_age_sec: float = WORKTREE_MAX_AGE_SEC,
                         now: Optional[float] = None) -> list[str]:
    """Release this subject's isolated worktrees untouched for longer than ``max_age_sec``; returns the
    unit ids reaped. Runs on each provision so the leak is bounded without a separate janitor. Age is the
    newest mtime of the worktree dir and its ``.git`` pointer. Best-effort — never raises."""
    reaped: list[str] = []
    try:
        base = Path(root) / WORKTREE_DIRNAME / subject
        if not base.is_dir():
            return reaped
        cutoff = (time.time() if now is None else now) - max_age_sec
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            marks = [entry.stat().st_mtime]
            if (entry / ".git").exists():
                marks.append((entry / ".git").stat().st_mtime)
            if max(marks) < cutoff:
                release_worktree(root, subject, entry.name)
                reaped.append(entry.name)
    except Exception:  # noqa: BLE001 — housekeeping must never fail a dispatch
        log.exception("stale worktree reap raised for subject=%s", subject)
    return reaped
