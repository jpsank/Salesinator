"""workspace_git_sync — GitHub push / pull / status for a workspace with a home remote.

Proves the sync lifecycle on REAL git over LOCAL repos (no network): a clone keeps a token-free
``origin`` → push a local commit (ff) → pull a remote commit (ff) → a divergence is REFUSED (no
merge/rebase/force) → ahead/behind status is computed locally → a home-less workspace reports none →
tokens never leak into error messages (P15).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_git_sync import (
    MergeError,
    RemoteSyncError,
    home_remote,
    merge_branch,
    pull_origin,
    push_origin,
    remote_status,
)

TOKEN = "ghp_SECRET_token_123"


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _bare(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(path, "init", "-q", "--bare", "-b", "main")
    return path


def _clone(remote: Path, dest: Path, *, seed: bool = False) -> Path:
    if seed:  # give the bare repo an initial commit so a clone lands on a real branch
        tmp = dest.parent / (dest.name + "_seed")
        subprocess.run(["git", "clone", "-q", str(remote), str(tmp)], check=True, capture_output=True, text=True)
        _run(tmp, "config", "user.email", "t@t")
        _run(tmp, "config", "user.name", "t")
        (tmp / "README.md").write_text("v0\n")
        _run(tmp, "add", "-A")
        _run(tmp, "commit", "-q", "-m", "init")
        _run(tmp, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True, capture_output=True, text=True)
    _run(dest, "config", "user.email", "t@t")
    _run(dest, "config", "user.name", "t")
    return dest


def _commit(ws: Path, text: str) -> str:
    (ws / "note.md").write_text(text)
    _run(ws, "add", "-A")
    _run(ws, "commit", "-q", "-m", text)
    return _run(ws, "rev-parse", "HEAD")


def test_home_remote_is_origin_for_a_clone(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    ws = _clone(bare, tmp_path / "ws", seed=True)
    home = home_remote(ws)
    assert home is not None
    name, url = home
    assert name == "origin"
    assert TOKEN not in url  # token-free (P15)


def test_status_no_home_when_no_remote(tmp_path):
    ws = tmp_path / "born"
    ws.mkdir()
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t")
    _run(ws, "config", "user.name", "t")
    _commit(ws, "local only")
    s = remote_status(ws)
    assert s.has_home is False
    assert s.branch == "main"
    assert s.ahead == 0 and s.behind == 0


def test_push_fast_forwards_the_home(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    ws = _clone(bare, tmp_path / "ws", seed=True)
    sha = _commit(ws, "local work")
    # ahead by 1 before the push
    before = remote_status(ws)
    assert before.ahead == 1 and before.behind == 0
    r = push_origin(ws, token=TOKEN)
    assert r.head_sha == sha and r.branch == "main" and r.remote == "origin"
    # the bare remote now carries the commit
    assert _run(bare, "rev-parse", "main") == sha
    # and status is back in sync (tracking ref advanced locally, no re-fetch)
    after = remote_status(ws)
    assert after.ahead == 0 and after.behind == 0


def test_pull_fast_forwards_from_the_home(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    # B pushes a commit up
    sha = _commit(b, "from B")
    push_origin(b, token=TOKEN)
    # A pulls it (ff)
    r = pull_origin(a, token=TOKEN)
    assert r.updated is True and r.behind_before == 1
    assert _run(a, "rev-parse", "HEAD") == sha


def test_pull_with_no_new_commits_is_a_noop(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    r = pull_origin(a, token=TOKEN)
    assert r.updated is False and r.behind_before == 0


def test_pull_refuses_a_divergence(tmp_path):
    """Local has a commit the remote doesn't → not a fast-forward → refuse (no merge/rebase/force)."""
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    _commit(b, "from B"); push_origin(b, token=TOKEN)
    _commit(a, "diverging local on A")  # A now has a local commit not on the remote
    with pytest.raises(RemoteSyncError) as exc:
        pull_origin(a, token=TOKEN)
    assert "fast-forward" in str(exc.value).lower()
    assert TOKEN not in str(exc.value)


def test_push_non_fast_forward_is_refused(tmp_path):
    """The remote moved ahead → a push is rejected (never a force push), with a token-free message."""
    bare = _bare(tmp_path / "remote.git")
    a = _clone(bare, tmp_path / "a", seed=True)
    b = _clone(bare, tmp_path / "b")
    _commit(b, "from B"); push_origin(b, token=TOKEN)
    _commit(a, "local on A")  # A diverges without pulling
    with pytest.raises(RemoteSyncError) as exc:
        push_origin(a, token=TOKEN)
    assert TOKEN not in str(exc.value)
    assert "force" in str(exc.value).lower() or "reject" in str(exc.value).lower()


def test_push_requires_a_token(tmp_path):
    bare = _bare(tmp_path / "remote.git")
    ws = _clone(bare, tmp_path / "ws", seed=True)
    with pytest.raises(ValueError):
        push_origin(ws, token="  ")


def _local_repo(tmp_path: Path) -> Path:
    """A plain local-only repo — merge_branch never touches a remote, so no bare/clone needed."""
    ws = tmp_path / "local"
    ws.mkdir()
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t")
    _run(ws, "config", "user.name", "t")
    _commit(ws, "init")
    return ws


def test_merge_branch_merges_cleanly(tmp_path):
    ws = _local_repo(tmp_path)
    _run(ws, "checkout", "-q", "-b", "feature")
    sha = _commit(ws, "feature work")
    _run(ws, "checkout", "-q", "main")
    head = merge_branch(ws, branch="feature", into="main")
    assert head != sha  # a real --no-ff merge commit, not a fast-forward onto feature's own sha
    assert _run(ws, "log", "-1", "--format=%P").count(" ") == 1  # two parents == a merge commit


def test_merge_branch_rejects_a_flag_like_branch_name(tmp_path):
    """A branch/into value that LOOKS like a git flag (a leading '-') must never reach the git
    subprocess as an argument — it would be interpreted as a flag instead of a ref name."""
    ws = _local_repo(tmp_path)
    with pytest.raises(MergeError) as exc:
        merge_branch(ws, branch="--abort", into="main")
    assert "invalid branch name" in str(exc.value)
    # never even attempted — the workspace is untouched, still on its original branch
    assert _run(ws, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_merge_branch_restores_original_branch_on_conflict(tmp_path):
    """A caller working on some OTHER branch must find it exactly as they left it after a failed
    merge — not silently stranded on `into` because the merge needed to check it out first."""
    ws = _local_repo(tmp_path)
    _run(ws, "checkout", "-q", "-b", "feature")
    (ws / "note.md").write_text("feature version")
    _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "feature edits note.md")
    _run(ws, "checkout", "-q", "main")
    (ws / "note.md").write_text("main version")  # conflicting edit to the same file
    _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "main edits note.md")
    _run(ws, "checkout", "-q", "-b", "working-on-something-else")

    with pytest.raises(MergeError):
        merge_branch(ws, branch="feature", into="main")

    # left exactly where the caller was, not stranded on `main`
    assert _run(ws, "rev-parse", "--abbrev-ref", "HEAD") == "working-on-something-else"
    # and no half-finished merge state left behind either
    assert not (ws / ".git" / "MERGE_HEAD").exists()
