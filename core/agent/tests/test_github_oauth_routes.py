"""The "Connect GitHub" OAuth routes — /api/workspace/git-token/oauth/{authorize,callback}.
Proves the signed-state handoff (no session on the callback leg) lands the token in the SAME
per-user store a manually pasted PAT would (git_credentials), and that the whole thing is a no-op
(503) when unconfigured — the paste-a-PAT path above it is untouched either way.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane import git_credentials as git_creds
from control_plane import github_oauth
from control_plane.api import create_app
from control_plane.dispatch import Dispatcher
from control_plane.workspace_reader import WorkspaceReader
from shared.config import load_settings


class _FakeRuntime:
    def spawn(self, workload_id, profile, env): return workload_id
    def await_done(self, workload_id, timeout_sec=0.0): return "completed"


class _FakeIdentity:
    def mint(self, subject, launcher, workspaces, tools): return "tok"


def _client(root: Path, **settings_overrides) -> TestClient:
    return TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(root), **settings_overrides), _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(root)),
    ))


H = {"X-User-Id": "u_jane"}


def test_authorize_503_when_not_configured(tmp_path):
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(tmp_path)
    r = c.get("/api/workspace/git-token/oauth/authorize", headers=H, follow_redirects=False)
    assert r.status_code == 503


def test_git_token_get_reports_oauth_configured(tmp_path):
    """The Settings UI needs to know whether it's even worth showing "Connect" — not just whether
    the callback would 503, without the rep having to click it first to find out."""
    (tmp_path / "u_jane").mkdir(parents=True)
    unconfigured = _client(tmp_path)
    assert unconfigured.get("/api/workspace/git-token", headers=H).json()["oauth_configured"] is False

    configured = _client(
        tmp_path, github_oauth_client_id="cid",
        github_oauth_redirect_uri="http://localhost:18100/api/workspace/git-token/oauth/callback",
    )
    assert configured.get("/api/workspace/git-token", headers=H).json()["oauth_configured"] is True


def test_authorize_redirects_to_github_with_signed_state(tmp_path):
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(
        tmp_path, github_oauth_client_id="cid",
        github_oauth_redirect_uri="http://localhost:18100/api/workspace/git-token/oauth/callback",
    )
    r = c.get("/api/workspace/git-token/oauth/authorize", headers=H, follow_redirects=False)
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert location.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=cid" in location
    # the state encodes u_jane, signed — pull it out and verify it decodes back to the same subject
    import urllib.parse
    state = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["state"][0]
    assert github_oauth.verify_state(state=state, secret="dev-dispatch-signing-key") == "u_jane"


def test_callback_success_stores_token_for_the_subject_the_state_names(tmp_path, monkeypatch):
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(
        tmp_path, github_oauth_client_id="cid", github_oauth_client_secret="csecret",
        github_oauth_redirect_uri="http://localhost:18100/api/workspace/git-token/oauth/callback",
        terminal_url="http://localhost:13000",
    )

    class _Resp:
        def read(self): return json.dumps({"access_token": "ghu_abc123"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", lambda req, timeout=10: _Resp())

    state = github_oauth.sign_state(subject="u_jane", secret="dev-dispatch-signing-key")
    r = c.get(f"/api/workspace/git-token/oauth/callback?code=the-code&state={state}", follow_redirects=False)
    assert r.headers["location"] == "http://localhost:13000/?settings=integrations&github_connected=1"
    assert git_creds.read_github_token(tmp_path, "u_jane") == "ghu_abc123"


def test_callback_with_no_code_redirects_with_error(tmp_path):
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(tmp_path, terminal_url="http://localhost:13000")
    r = c.get("/api/workspace/git-token/oauth/callback", follow_redirects=False)
    assert "github_error=no_code" in r.headers["location"]


def test_callback_with_tampered_state_redirects_with_error_and_does_not_store(tmp_path):
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(
        tmp_path, github_oauth_client_id="cid", github_oauth_client_secret="csecret",
        github_oauth_redirect_uri="http://x/cb", terminal_url="http://localhost:13000",
    )
    forged_state = "u_jane:123:not-a-real-signature"
    r = c.get(f"/api/workspace/git-token/oauth/callback?code=x&state={forged_state}", follow_redirects=False)
    assert "github_error=" in r.headers["location"]
    assert git_creds.read_github_token(tmp_path, "u_jane") is None


def test_callback_state_for_a_different_subject_stores_under_that_subject_not_the_caller(tmp_path, monkeypatch):
    """The whole point of signing the subject INTO the state: the callback trusts the state, not
    any header on the callback request (there is none to trust — no cookie crosses this redirect)."""
    (tmp_path / "u_bob").mkdir(parents=True)
    c = _client(
        tmp_path, github_oauth_client_id="cid", github_oauth_client_secret="csecret",
        github_oauth_redirect_uri="http://x/cb", terminal_url="http://localhost:13000",
    )

    class _Resp:
        def read(self): return json.dumps({"access_token": "ghu_bob"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", lambda req, timeout=10: _Resp())
    state = github_oauth.sign_state(subject="u_bob", secret="dev-dispatch-signing-key")
    c.get(f"/api/workspace/git-token/oauth/callback?code=x&state={state}", follow_redirects=False)
    assert git_creds.read_github_token(tmp_path, "u_bob") == "ghu_bob"
    assert git_creds.read_github_token(tmp_path, "u_jane") is None
