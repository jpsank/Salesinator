"""workspace_worktree — per-invocation isolation for commit-capable turns.

Proves the concurrency-safety mechanism on REAL git over local repos (no network): two units
provisioned against the same subject get independent worktrees that can diverge without touching
each other, idempotent re-provision returns the SAME worktree, and release actually removes it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_worktree import (
    WorktreeError,
    provision_worktree,
    release_worktree,
    worktree_dir_for,
)


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _seed_baseline(root: Path, subject: str) -> Path:
    ws = root / subject
    ws.mkdir(parents=True)
    _run("init", "-q", "-b", "main", cwd=ws)
    _run("config", "user.email", "seed@test", cwd=ws)
    _run("config", "user.name", "seed", cwd=ws)
    (ws / "README.md").write_text("baseline\n")
    _run("add", "-A", cwd=ws)
    _run("commit", "-q", "-m", "seed", cwd=ws)
    return ws


def test_provision_creates_an_isolated_worktree(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")

    dest = provision_worktree(root, "product-repo", "unit-1")

    assert dest == worktree_dir_for(root, "product-repo", "unit-1")
    assert (dest / ".git").exists() or (dest / ".git").is_file()  # linked worktree marker file
    assert (dest / "README.md").read_text() == "baseline\n"


def test_two_units_get_independent_worktrees_that_can_diverge(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")

    a = provision_worktree(root, "product-repo", "unit-a")
    b = provision_worktree(root, "product-repo", "unit-b")
    assert a != b

    _run("checkout", "-q", "-b", "feature/a", cwd=a)
    (a / "a.txt").write_text("a\n")
    _run("add", "-A", cwd=a)
    _run("commit", "-q", "-m", "a", cwd=a)

    _run("checkout", "-q", "-b", "feature/b", cwd=b)
    (b / "b.txt").write_text("b\n")
    _run("add", "-A", cwd=b)
    _run("commit", "-q", "-m", "b", cwd=b)

    # Neither worktree's branch/files leaked into the other, and the baseline itself is untouched.
    assert (a / "a.txt").exists() and not (a / "b.txt").exists()
    assert (b / "b.txt").exists() and not (b / "a.txt").exists()
    assert _run("branch", "--show-current", cwd=root / "product-repo").stdout.strip() == "main"


def test_provision_is_idempotent_for_the_same_unit_id(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")

    first = provision_worktree(root, "product-repo", "unit-1")
    (first / "mine.txt").write_text("still here\n")
    second = provision_worktree(root, "product-repo", "unit-1")

    assert second == first
    assert (second / "mine.txt").read_text() == "still here\n"  # not re-created from scratch


def _seed_baseline_no_identity(root: Path, subject: str) -> Path:
    """Same as `_seed_baseline`, but the initial commit uses one-off `-c` flags instead of persisting
    `user.name`/`user.email` into the repo's own config — so this test can prove `provision_worktree`
    actually SETS the identity, not just find it already there from seeding."""
    ws = root / subject
    ws.mkdir(parents=True)
    _run("init", "-q", "-b", "main", cwd=ws)
    (ws / "README.md").write_text("baseline\n")
    _run("add", "-A", cwd=ws)
    _run("-c", "user.email=seed@test", "-c", "user.name=seed", "commit", "-q", "-m", "seed", cwd=ws)
    return ws


def test_provision_sets_identity_and_installs_signoff_hook(tmp_path):
    root = tmp_path / "workspaces"
    baseline = _seed_baseline_no_identity(root, "product-repo")

    dest = provision_worktree(
        root, "product-repo", "unit-1", principal={"name": "Julian Sanker", "email": "julian@sankergroup.org"},
    )

    assert _run("config", "--get", "user.name", cwd=baseline).stdout.strip() == "Julian Sanker"
    assert _run("config", "--get", "user.email", cwd=baseline).stdout.strip() == "julian@sankergroup.org"

    hook = baseline / ".git" / "hooks" / "prepare-commit-msg"
    assert hook.exists()

    _run("checkout", "-q", "-b", "feature/x", cwd=dest)
    (dest / "x.txt").write_text("x\n")
    _run("add", "-A", cwd=dest)
    _run("commit", "-q", "-m", "x", cwd=dest)
    msg = _run("log", "-1", "--format=%B", cwd=dest).stdout
    assert "Signed-off-by: Julian Sanker <julian@sankergroup.org>" in msg


def test_provision_writes_the_identity_into_the_repo_even_when_the_host_has_a_global_one(tmp_path, monkeypatch):
    """A host with its own global git identity made the "already has one?" check pass, so the repo itself never got
    the principal's — and a worker container, which has no global config, committed with no identity the signoff hook
    could read: no Signed-off-by, and the push was refused."""
    global_cfg = tmp_path / "global.gitconfig"
    global_cfg.write_text("[user]\n\tname = Host Person\n\temail = host@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    root = tmp_path / "workspaces"
    baseline = _seed_baseline_no_identity(root, "product-repo")

    provision_worktree(root, "product-repo", "unit-1", principal={"name": "Julian Sanker", "email": "julian@sankergroup.org"})

    assert _run("config", "--local", "--get", "user.name", cwd=baseline).stdout.strip() == "Julian Sanker"
    assert _run("config", "--local", "--get", "user.email", cwd=baseline).stdout.strip() == "julian@sankergroup.org"


def test_provision_never_overrides_an_existing_hooks_path(tmp_path):
    root = tmp_path / "workspaces"
    baseline = _seed_baseline(root, "product-repo")
    _run("config", "core.hooksPath", ".githooks", cwd=baseline)
    (baseline / ".githooks").mkdir()
    marker = baseline / ".githooks" / "prepare-commit-msg"
    marker.write_text("#!/bin/sh\nexit 0\n")
    marker.chmod(0o755)

    provision_worktree(root, "product-repo", "unit-1", principal={"name": "N", "email": "e@e"})

    # our hook must NOT have been installed into the repo's own default hooks dir
    assert not (baseline / ".git" / "hooks" / "prepare-commit-msg").exists()


def test_provision_runs_optional_setup_command(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")

    dest = provision_worktree(
        root, "product-repo", "unit-1", setup_cmd="echo ran > setup-ran.txt",
    )

    assert (dest / "setup-ran.txt").read_text().strip() == "ran"


def test_provision_raises_loudly_on_setup_command_failure(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")

    with pytest.raises(WorktreeError, match="setup command failed"):
        provision_worktree(root, "product-repo", "unit-1", setup_cmd="exit 1")


def test_provision_raises_loudly_when_subject_has_no_baseline(tmp_path):
    root = tmp_path / "workspaces"
    with pytest.raises(WorktreeError):
        provision_worktree(root, "no-such-subject", "unit-1")


def test_release_removes_the_worktree(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")
    dest = provision_worktree(root, "product-repo", "unit-1")
    assert dest.exists()

    release_worktree(root, "product-repo", "unit-1")

    assert not dest.exists()


def test_release_of_an_unprovisioned_unit_is_a_safe_noop(tmp_path):
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")
    release_worktree(root, "product-repo", "never-provisioned")  # must not raise


def test_setup_command_does_not_inherit_the_control_planes_secrets(tmp_path, monkeypatch):
    """The setup command runs the product repo's own dependency scripts, so agent-api's secrets stay
    out of its env; only variables the operator names explicitly are passed through."""
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "s3cret"); monkeypatch.setenv("REGISTRY_TOKEN", "r")
    monkeypatch.setenv("VEXA_WORKSPACE_WORKTREE_SETUP_ENV", "REGISTRY_TOKEN")
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")

    dest = provision_worktree(root, "product-repo", "unit-1",
                              setup_cmd='echo "[$VEXA_INTERNAL_API_SECRET][$REGISTRY_TOKEN]" > env.txt')

    assert (dest / "env.txt").read_text().strip() == "[][r]"


def test_setup_command_timeout_is_a_worktree_error(tmp_path, monkeypatch):
    import subprocess as sp
    import control_plane.workspace_worktree as ww
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")
    def boom(*a, **k): raise sp.TimeoutExpired(cmd="x", timeout=600)
    real = ww.subprocess.run
    monkeypatch.setattr(ww.subprocess, "run", lambda cmd, *a, **k: boom() if k.get("shell") else real(cmd, *a, **k))
    with pytest.raises(WorktreeError, match="timed out"):
        provision_worktree(root, "product-repo", "unit-1", setup_cmd="sleep 1")


def test_reap_stale_worktrees_releases_only_the_old_ones(tmp_path):
    from control_plane.workspace_worktree import reap_stale_worktrees, worktree_dir_for
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")
    old = provision_worktree(root, "product-repo", "unit-old")
    fresh = provision_worktree(root, "product-repo", "unit-fresh")
    import os, time
    ancient = time.time() - 10 * 24 * 3600
    for p in (old, old / ".git"):
        os.utime(p, (ancient, ancient))

    assert reap_stale_worktrees(root, "product-repo") == ["unit-old"]
    assert not old.exists() and fresh.exists()
    assert reap_stale_worktrees(root, "product-repo") == []   # idempotent


def test_provision_reaps_the_subjects_stale_worktrees(tmp_path):
    import os, time
    root = tmp_path / "workspaces"
    _seed_baseline(root, "product-repo")
    old = provision_worktree(root, "product-repo", "unit-old")
    ancient = time.time() - 10 * 24 * 3600
    for p in (old, old / ".git"):
        os.utime(p, (ancient, ancient))
    provision_worktree(root, "product-repo", "unit-new")
    assert not old.exists()
