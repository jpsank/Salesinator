"""gates_runner — the two compliance checks run before pushing an isolated worktree's branch.

Both proved over real git repos (no network): check_commit_compliance reads HEAD's message
directly; run_configured_pre_push_hook respects whatever hook the repo itself has configured
(or none) — never a hardcoded call to any specific repo's own tooling.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from control_plane.gates_runner import check_commit_compliance, run_configured_pre_push_hook


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _repo_with_commit(tmp_path: Path, message: str) -> Path:
    ws = tmp_path / "repo"
    ws.mkdir()
    _run("init", "-q", "-b", "main", cwd=ws)
    _run("config", "user.email", "t@test", cwd=ws)
    _run("config", "user.name", "t", cwd=ws)
    (ws / "f.txt").write_text("x\n")
    _run("add", "-A", cwd=ws)
    _run("commit", "-q", "-m", message, cwd=ws)
    return ws


# ── check_commit_compliance ────────────────────────────────────────────────────

def test_passes_with_no_expected_signoff_configured(tmp_path):
    ws = _repo_with_commit(tmp_path, "add feature")
    assert check_commit_compliance(ws, expected_signoff=None) == []


def test_passes_when_signoff_present_and_no_trailer(tmp_path):
    ws = _repo_with_commit(tmp_path, "add feature\n\nSigned-off-by: Julian Sanker <julian@sankergroup.org>")
    assert check_commit_compliance(ws, expected_signoff="Julian Sanker <julian@sankergroup.org>") == []


def test_fails_when_expected_signoff_missing(tmp_path):
    ws = _repo_with_commit(tmp_path, "add feature")
    failures = check_commit_compliance(ws, expected_signoff="Julian Sanker <julian@sankergroup.org>")
    assert len(failures) == 1
    assert "Signed-off-by" in failures[0]


def test_fails_when_co_authored_by_claude_present(tmp_path):
    ws = _repo_with_commit(
        tmp_path,
        "add feature\n\nSigned-off-by: Julian Sanker <julian@sankergroup.org>\n"
        "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
    )
    failures = check_commit_compliance(ws, expected_signoff="Julian Sanker <julian@sankergroup.org>")
    assert len(failures) == 1
    assert "Co-Authored-By" in failures[0]


def test_reports_both_failures_independently(tmp_path):
    ws = _repo_with_commit(tmp_path, "add feature\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>")
    failures = check_commit_compliance(ws, expected_signoff="Julian Sanker <julian@sankergroup.org>")
    assert len(failures) == 2


# ── run_configured_pre_push_hook ───────────────────────────────────────────────

def test_passes_when_no_hook_configured_at_all(tmp_path):
    ws = _repo_with_commit(tmp_path, "x")
    assert run_configured_pre_push_hook(ws) == []


def test_runs_the_default_git_hooks_pre_push_when_present(tmp_path):
    ws = _repo_with_commit(tmp_path, "x")
    hook = ws / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    failures = run_configured_pre_push_hook(ws)
    assert len(failures) == 1
    assert "pre-push hook failed" in failures[0]


def test_a_passing_default_hook_reports_no_failures(tmp_path):
    ws = _repo_with_commit(tmp_path, "x")
    hook = ws / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    assert run_configured_pre_push_hook(ws) == []


def test_respects_a_custom_core_hookspath(tmp_path):
    ws = _repo_with_commit(tmp_path, "x")
    _run("config", "core.hooksPath", ".githooks", cwd=ws)
    (ws / ".githooks").mkdir()
    hook = ws / ".githooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho custom hook ran >&2\nexit 1\n")
    hook.chmod(0o755)
    failures = run_configured_pre_push_hook(ws)
    assert len(failures) == 1
    assert "custom hook ran" in failures[0]


def test_a_non_executable_hook_file_is_treated_as_not_configured(tmp_path):
    ws = _repo_with_commit(tmp_path, "x")
    hook = ws / ".git" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 1\n")  # deliberately NOT chmod +x
    assert run_configured_pre_push_hook(ws) == []


def test_the_pre_push_hook_never_sees_the_control_planes_secrets(tmp_path, monkeypatch):
    """The hook's tree is writable by the agent it gates, so the control plane's env must not leak in."""
    monkeypatch.setenv("VEXA_INTERNAL_API_SECRET", "s3cret"); monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    ws = _repo_with_commit(tmp_path, "x")
    hook = ws / ".git" / "hooks" / "pre-push"
    hook.write_text('#!/bin/sh\n[ -z "$VEXA_INTERNAL_API_SECRET$ANTHROPIC_API_KEY" ] || { echo leaked; exit 1; }\n')
    hook.chmod(0o755)
    assert run_configured_pre_push_hook(ws) == []
