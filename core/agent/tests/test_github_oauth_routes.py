"""The "Connect GitHub" OAuth routes — /api/workspace/git-token/oauth/{authorize,callback}.
Proves the signed-state handoff (no session on the callback leg) lands the token in the SAME
per-user store a manually pasted PAT would (git_credentials), and that the whole thing is a no-op
(503) when unconfigured — the paste-a-PAT path above it is untouched either way.

Always the CALLER's own identity — there is no separate OAuth connection for a "product repo" or
any other on-behalf-of workspace action; see the workspace_delegate_subject (?for=/for_subject)
tests below for how those reuse the caller's OWN saved token instead.
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


def test_git_token_get_reports_oauth_configured_and_target_subject(tmp_path):
    """The Settings UI needs to know whether it's even worth showing "Connect" — not just whether
    the callback would 503, without the rep having to click it first to find out. `target_subject`
    is the configured workspace_delegate_subject, so a client never hardcodes a value this
    deployment owns (e.g. the product-repo picker asks for THIS instead of a baked-in string)."""
    (tmp_path / "u_jane").mkdir(parents=True)
    unconfigured = _client(tmp_path, workspace_delegate_subject="product-repo")
    body = unconfigured.get("/api/workspace/git-token", headers=H).json()
    assert body["oauth_configured"] is False
    assert body["target_subject"] == "product-repo"

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


def test_authorize_ignores_a_for_param_always_uses_the_caller(tmp_path):
    """authorize/repos never supported acting on behalf of another subject — only the workspace
    init/attached/swap endpoints do (they don't need a SEPARATE OAuth connection to do it)."""
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(
        tmp_path, github_oauth_client_id="cid",
        github_oauth_redirect_uri="http://localhost:18100/api/workspace/git-token/oauth/callback",
        workspace_delegate_subject="product-repo",
    )
    r = c.get("/api/workspace/git-token/oauth/authorize?for=product-repo", headers=H, follow_redirects=False)
    assert r.status_code in (302, 307)
    import urllib.parse
    state = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)["state"][0]
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


def test_repos_requires_a_connected_token_first(tmp_path):
    (tmp_path / "u_jane").mkdir(parents=True)
    c = _client(tmp_path)
    r = c.get("/api/workspace/git-token/oauth/repos", headers=H)
    assert r.status_code == 409


def test_repos_lists_the_callers_own_repos(tmp_path, monkeypatch):
    git_creds.set_github_token(tmp_path, "u_jane", "ghu_janes")
    c = _client(tmp_path)

    class _Resp:
        def read(self):
            return json.dumps([
                {"full_name": "acme/api", "clone_url": "https://github.com/acme/api.git",
                 "default_branch": "main", "private": True},
            ]).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", lambda req, timeout=10: _Resp())
    r = c.get("/api/workspace/git-token/oauth/repos", headers=H)
    assert r.status_code == 200
    assert r.json() == {"repos": [
        {"full_name": "acme/api", "clone_url": "https://github.com/acme/api.git",
         "default_branch": "main", "private": True},
    ]}


# ── workspace_delegate_subject (?for=/for_subject) — init/attached/swap only, so a "product repo"
# never needs its OWN separate OAuth connection: it reuses whoever's already connected GitHub ──────

def test_swap_for_the_shared_subject_uses_the_callers_own_token_and_mounts_under_that_subject(tmp_path, monkeypatch):
    """Real git over a local repo — no network (same pattern as test_api.py's swap coverage). Two
    things proved together: (1) ?for=/for_subject redirects WHICH workspace gets swapped, not just
    accepted; (2) the CALLER's own saved token authenticates it — there's no separate product-repo
    OAuth connection to maintain — and a copy lands under the target subject's own credential store
    too, so a later op that runs AS that subject (the eventual push) can still authenticate."""
    import subprocess

    monkeypatch.setenv("VEXA_ALLOW_LOCAL_REPO_ROOT", str(tmp_path))
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    # A clone needs its OWN CLAUDE.md to be treated as a "compliant" workspace (validate_seed) — else
    # workspace_attach nests it under kg/<slug>/ instead of using it as the workspace root directly.
    (origin / "MARK").write_text("CUSTOM\n"); (origin / "CLAUDE.md").write_text("CUSTOM ROOT\n")
    run("add", "-A"); run("commit", "-q", "-m", "x")

    workspaces = tmp_path / "ws"
    git_creds.set_github_token(workspaces, "u_jane", "ghu_janes_own_token")
    c = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(workspaces), workspace_delegate_subject="product-repo"),
                   _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    r = c.post("/api/workspace/swap", headers=H,
               json={"repo": str(origin), "ref": "main", "for_subject": "product-repo"})
    assert r.status_code == 200
    assert r.json()["subject"] == "product-repo"
    assert (workspaces / "product-repo" / "MARK").read_text() == "CUSTOM\n"
    assert not (workspaces / "u_jane").exists()  # the caller's own workspace was never touched
    # the caller's token got copied under the target subject too — a later server-to-server op
    # running AS "product-repo" (the eventual push) can authenticate without a separate connection
    assert git_creds.read_github_token(workspaces, "product-repo") == "ghu_janes_own_token"


def test_swap_for_the_shared_subject_never_stores_a_one_time_body_token(tmp_path, monkeypatch):
    """A `token` passed in the body is one-time (P15: never stored) — it may authenticate THIS clone
    but must not become the shared identity's persisted credential."""
    import subprocess

    monkeypatch.setenv("VEXA_ALLOW_LOCAL_REPO_ROOT", str(tmp_path))
    origin = tmp_path / "origin"; origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-q", "-b", "main"); run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("CUSTOM ROOT\n"); run("add", "-A"); run("commit", "-q", "-m", "x")
    workspaces = tmp_path / "ws"
    c = TestClient(create_app(
        Dispatcher(load_settings(workspaces_dir=str(workspaces), workspace_delegate_subject="product-repo"),
                   _FakeRuntime(), _FakeIdentity()),
        reader=WorkspaceReader(str(workspaces)),
    ))
    r = c.post("/api/workspace/swap", headers=H,
               json={"repo": str(origin), "ref": "main", "for_subject": "product-repo", "token": "ghp_one_time"})
    assert r.status_code == 200
    assert git_creds.read_github_token(workspaces, "product-repo") is None


def test_swap_for_disallowed_subject_is_refused(tmp_path):
    c = _client(tmp_path, workspace_delegate_subject="product-repo")
    r = c.post("/api/workspace/swap", headers=H, json={"repo": "https://github.com/acme/api.git", "for_subject": "someone-else"})
    assert r.status_code == 403


def test_init_for_the_shared_subject_seeds_it_not_the_caller(tmp_path, monkeypatch):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "CLAUDE.md").write_text("SEED\n")
    monkeypatch.setenv("VEXA_WORKSPACE_SEED_DIR", str(seed))
    c = _client(tmp_path, workspace_delegate_subject="product-repo")
    r = c.post("/api/workspace/init?for=product-repo", headers=H)
    assert r.status_code == 201
    assert r.json()["seeded"] is True
    assert (tmp_path / "product-repo" / "CLAUDE.md").read_text() == "SEED\n"
    assert not (tmp_path / "u_jane").exists()


def test_init_for_disallowed_subject_is_refused(tmp_path):
    c = _client(tmp_path, workspace_delegate_subject="product-repo")
    r = c.post("/api/workspace/init?for=someone-else", headers=H)
    assert r.status_code == 403


def test_attached_for_the_shared_subject_reports_its_own_empty_shape(tmp_path):
    """Safe to call before any attach — same "empty shape" guarantee attached_workspaces() gives
    any brand-new subject, proving ?for= reaches this endpoint rather than erroring or 403ing."""
    c = _client(tmp_path, workspace_delegate_subject="product-repo")
    r = c.get("/api/workspace/attached?for=product-repo", headers=H)
    assert r.status_code == 200
    assert "slots" in r.json() and "active_set" in r.json()


def test_git_token_get_verify_reports_whether_github_still_accepts_the_saved_token(tmp_path, monkeypatch):
    import io, json as _json, urllib.error
    git_creds.set_github_token(tmp_path, "u_jane", "ghu_saved")
    c = _client(tmp_path)

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", lambda req, timeout=10: _Resp(_json.dumps({"login": "jane"}).encode()))
    assert c.get("/api/workspace/git-token?verify=true", headers=H).json()["valid"] is True

    def _401(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Bad credentials", {}, None)
    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", _401)
    assert c.get("/api/workspace/git-token?verify=true", headers=H).json()["valid"] is False

    def _500(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", _500)
    assert c.get("/api/workspace/git-token?verify=true", headers=H).json()["valid"] is None   # no verdict ≠ rejected

    # without ?verify there is no GitHub call and no verdict, so the status poll stays free
    def _boom(req, timeout=10): raise AssertionError("must not call GitHub")
    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", _boom)
    assert "valid" not in c.get("/api/workspace/git-token", headers=H).json()


def test_repos_reports_a_rejected_saved_token_as_a_reconnect_prompt_not_a_gateway_fault(tmp_path, monkeypatch):
    import urllib.error
    git_creds.set_github_token(tmp_path, "u_jane", "ghu_revoked")
    c = _client(tmp_path)

    def _401(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 401, "Bad credentials", {}, None)

    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", _401)
    r = c.get("/api/workspace/git-token/oauth/repos", headers=H)
    assert r.status_code == 409
    assert "reconnect" in r.json()["detail"].lower()

    def _500(req, timeout=10):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)

    monkeypatch.setattr(github_oauth.urllib.request, "urlopen", _500)
    assert c.get("/api/workspace/git-token/oauth/repos", headers=H).status_code == 502   # a real upstream fault stays a 502
