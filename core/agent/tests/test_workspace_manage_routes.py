"""The workspace MANAGE routes — git-remote-status, push, pull, purpose — wired over _manage_dir.

Proves the HTTP surface resolves the caller's own (primary) workspace by path, gates on the header
identity (P20), and round-trips purpose + reports git sync state. Real git, local remotes, no network.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _client(root: Path) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root)), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
    ))


def _seed_primary(root: Path, subject: str, *, with_origin: bool = False) -> Path:
    """The subject's primary workspace lives at <root>/<subject> (the seed slot)."""
    ws = root / subject
    if with_origin:
        bare = root / "remote.git"; bare.mkdir(parents=True)
        _run(bare, "init", "-q", "--bare", "-b", "main")
        subprocess.run(["git", "clone", "-q", str(bare), str(ws)], check=True, capture_output=True, text=True)
        _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
        (ws / "README.md").write_text("v0\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "init")
        _run(ws, "push", "-q", "origin", "main")
    else:
        ws.mkdir(parents=True)
        _run(ws, "init", "-q", "-b", "main"); _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
        (ws / "README.md").write_text("v0\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "init")
    return ws


H = {"X-User-Id": "u_jane"}


def test_git_remote_status_no_home(tmp_path):
    _seed_primary(tmp_path, "u_jane", with_origin=False)
    c = _client(tmp_path)
    r = c.get("/api/workspace/git-remote-status", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["has_home"] is False and body["branch"] == "main"


def test_git_remote_status_reports_ahead_after_a_local_commit(tmp_path):
    ws = _seed_primary(tmp_path, "u_jane", with_origin=True)
    (ws / "note.md").write_text("local\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "local")
    c = _client(tmp_path)
    body = c.get("/api/workspace/git-remote-status", headers=H).json()
    assert body["has_home"] is True and body["remote"] == "origin"
    assert body["ahead"] == 1 and body["behind"] == 0 and body["tracked"] is True


def test_manage_is_scoped_to_the_header_identity(tmp_path):
    """Each caller manages only THEIR OWN workspace — purpose set by u_jane is invisible to u_bob (P20)."""
    _seed_primary(tmp_path, "u_jane")
    _seed_primary(tmp_path, "u_bob")
    c = _client(tmp_path)
    c.post("/api/workspace/purpose", headers=H, json={"purpose": "jane's deal room"})
    assert c.get("/api/workspace/purpose", headers={"X-User-Id": "u_bob"}).json()["purpose"] == ""


def test_purpose_roundtrip_via_routes(tmp_path):
    _seed_primary(tmp_path, "u_jane")
    c = _client(tmp_path)
    assert c.get("/api/workspace/purpose", headers=H).json()["purpose"] == ""
    r = c.post("/api/workspace/purpose", headers=H, json={"purpose": "  ACME deal room  "})
    assert r.status_code == 200 and r.json()["purpose"] == "ACME deal room"
    assert c.get("/api/workspace/purpose", headers=H).json()["purpose"] == "ACME deal room"


def test_push_via_route_fast_forwards(tmp_path):
    ws = _seed_primary(tmp_path, "u_jane", with_origin=True)
    (ws / "note.md").write_text("local\n"); _run(ws, "add", "-A"); sha = None
    _run(ws, "commit", "-q", "-m", "local"); sha = _run(ws, "rev-parse", "HEAD")
    c = _client(tmp_path)
    r = c.post("/api/workspace/push", headers=H, json={"token": "ghp_x"})
    assert r.status_code == 200, r.text
    assert r.json()["head_sha"] == sha
    # status now in sync
    body = c.get("/api/workspace/git-remote-status", headers=H).json()
    assert body["ahead"] == 0


# ── unit= (isolated per-turn worktrees, workspace_worktree.py) ─────────────────

def test_git_state_for_an_unprovisioned_unit_reads_as_not_ready(tmp_path):
    _seed_primary(tmp_path, "u_jane")
    c = _client(tmp_path)
    body = c.get("/api/workspace/git", headers=H, params={"unit": "never-provisioned"}).json()
    assert body == {"branch": "", "changes": [], "commits": []}


def test_git_state_for_a_provisioned_unit_reports_its_own_branch(tmp_path):
    from control_plane.workspace_worktree import provision_worktree
    root = tmp_path
    _seed_primary(root, "u_jane")
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")
    (dest / "x.txt").write_text("x\n"); _run(dest, "add", "-A")
    _run(dest, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x")

    c = _client(root)
    body = c.get("/api/workspace/git", headers=H, params={"unit": "unit-1"}).json()
    assert body["branch"] == "feature/x"
    # the plain baseline (no unit param) is completely untouched
    assert c.get("/api/workspace/git", headers=H).json()["branch"] == "main"


def test_push_via_unit_pushes_the_worktrees_branch_without_releasing_it(tmp_path):
    """Reproduced live: /api/workspace/pull-request is a SEPARATE, later call (sales_cycle's
    orchestrator always pushes, then opens a PR — sometimes across sweeps) that needs this SAME
    worktree directory to resolve the branch/remote. Releasing it here (the old behavior) left every
    PR-open call 400ing forever against an already-deleted directory. The worktree now survives push
    and is released by ws_pull_request instead (see the test below)."""
    from control_plane.workspace_worktree import provision_worktree
    root = tmp_path
    _seed_primary(root, "u_jane", with_origin=True)
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")
    (dest / "x.txt").write_text("x\n"); _run(dest, "add", "-A")
    _run(dest, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x")

    c = _client(root)
    r = c.post("/api/workspace/push", headers=H, json={"token": "ghp_x", "unit": "unit-1"})
    assert r.status_code == 200, r.text
    assert r.json()["branch"] == "feature/x"
    assert dest.exists()  # NOT released — the pull-request call still needs it


def test_push_via_unit_refuses_when_signoff_missing(tmp_path):
    from control_plane.workspace_worktree import provision_worktree
    root = tmp_path
    _seed_primary(root, "u_jane", with_origin=True)
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")
    (dest / "x.txt").write_text("x\n"); _run(dest, "add", "-A")
    _run(dest, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "no signoff here")

    c = _client(root)
    r = c.post("/api/workspace/push", headers=H, json={
        "token": "ghp_x", "unit": "unit-1", "expected_signoff": "Julian Sanker <julian@sankergroup.org>",
    })
    assert r.status_code == 409
    assert "Signed-off-by" in r.json()["detail"]
    assert dest.exists()  # refused before push — nothing released, nothing pushed


def test_push_via_unit_refuses_on_co_authored_by_claude_trailer(tmp_path):
    from control_plane.workspace_worktree import provision_worktree
    root = tmp_path
    _seed_primary(root, "u_jane", with_origin=True)
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")
    (dest / "x.txt").write_text("x\n"); _run(dest, "add", "-A")
    _run(dest, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m",
         "x\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>")

    c = _client(root)
    r = c.post("/api/workspace/push", headers=H, json={"token": "ghp_x", "unit": "unit-1"})
    assert r.status_code == 409
    assert "Co-Authored-By" in r.json()["detail"]


def test_push_via_unit_respects_a_configured_pre_push_hook(tmp_path):
    from control_plane.workspace_worktree import provision_worktree
    root = tmp_path
    ws = _seed_primary(root, "u_jane", with_origin=True)
    hook = ws / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho blocked by hook >&2\nexit 1\n")
    hook.chmod(0o755)
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")
    (dest / "x.txt").write_text("x\n"); _run(dest, "add", "-A")
    _run(dest, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x")

    c = _client(root)
    r = c.post("/api/workspace/push", headers=H, json={"token": "ghp_x", "unit": "unit-1"})
    assert r.status_code == 409
    assert "blocked by hook" in r.json()["detail"]


def test_pull_request_route_resolves_unit_and_calls_create_pull_request(tmp_path, monkeypatch):
    from control_plane.workspace_worktree import provision_worktree
    import control_plane.api as api_module

    root = tmp_path
    _seed_primary(root, "u_jane", with_origin=True)
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")

    calls = []

    def fake_create_pull_request(ws, *, title, body, base, token, **kwargs):
        calls.append({"ws": str(ws), "title": title, "body": body, "base": base, "token": token})
        return {"url": "https://github.com/acme/product/pull/7", "number": 7}

    monkeypatch.setattr(api_module, "create_pull_request", fake_create_pull_request)

    c = _client(root)
    r = c.post("/api/workspace/pull-request", headers=H, json={
        "unit": "unit-1", "token": "ghp_x", "title": "CSV export", "body": "wants it",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"url": "https://github.com/acme/product/pull/7", "number": 7}
    assert calls == [{"ws": str(dest), "title": "CSV export", "body": "wants it", "base": "main", "token": "ghp_x"}]
    assert not dest.exists()  # released HERE now — the last step of the isolated-turn lifecycle


def test_pull_request_route_does_not_release_the_worktree_on_failure(tmp_path, monkeypatch):
    """A failed PR-open (e.g. a transient GitHub error) must leave the worktree in place — the sweep
    retries the SAME call next time, and it still needs the directory to resolve branch/remote."""
    from control_plane.workspace_worktree import provision_worktree
    import control_plane.api as api_module
    from control_plane.workspace_publish import PullRequestError

    root = tmp_path
    _seed_primary(root, "u_jane", with_origin=True)
    dest = provision_worktree(root, "u_jane", "unit-1")
    _run(dest, "checkout", "-q", "-b", "feature/x")

    def failing_create_pull_request(ws, **kwargs):
        raise PullRequestError("GitHub unreachable")

    monkeypatch.setattr(api_module, "create_pull_request", failing_create_pull_request)

    c = _client(root)
    r = c.post("/api/workspace/pull-request", headers=H, json={
        "unit": "unit-1", "token": "ghp_x", "title": "x", "body": "y",
    })
    assert r.status_code == 502
    assert dest.exists()  # NOT released — the next sweep retry needs it


def test_pull_request_route_requires_a_token(tmp_path):
    _seed_primary(tmp_path, "u_jane")
    c = _client(tmp_path)
    r = c.post("/api/workspace/pull-request", headers=H, json={"title": "x", "body": "y"})
    assert r.status_code == 400


def test_push_without_unit_is_unaffected_by_worktree_compliance_checks(tmp_path):
    """The interactive Settings-page push (no `unit`) must pay none of this — plain workspaces are
    never signoff/hook-checked, matching today's existing behavior exactly (a commit with no signoff
    and no configured hook still pushes fine, same as before this feature existed)."""
    ws = _seed_primary(tmp_path, "u_jane", with_origin=True)
    (ws / "note.md").write_text("local\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "local, no signoff")
    c = _client(tmp_path)
    r = c.post("/api/workspace/push", headers=H, json={"token": "ghp_x"})
    assert r.status_code == 200, r.text
