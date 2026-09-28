"""Tests for github_oauth — the "Connect GitHub" flow, its state signing, and the code exchange."""
import json
import urllib.error

import pytest

from control_plane import github_oauth as gh


def test_sign_and_verify_state_roundtrip():
    state = gh.sign_state(subject="u_jane", secret="s3cret")
    assert gh.verify_state(state=state, secret="s3cret") == "u_jane"


def test_verify_state_rejects_wrong_secret():
    state = gh.sign_state(subject="u_jane", secret="s3cret")
    with pytest.raises(gh.GitHubOAuthError):
        gh.verify_state(state=state, secret="wrong-secret")


def test_verify_state_rejects_tampered_subject():
    state = gh.sign_state(subject="u_jane", secret="s3cret")
    _, ts, sig = state.split(":", 2)
    forged = f"u_attacker:{ts}:{sig}"
    with pytest.raises(gh.GitHubOAuthError):
        gh.verify_state(state=forged, secret="s3cret")


def test_verify_state_rejects_malformed():
    with pytest.raises(gh.GitHubOAuthError):
        gh.verify_state(state="not-enough-parts", secret="s3cret")


def test_verify_state_rejects_stale():
    state = gh.sign_state(subject="u_jane", secret="s3cret")
    _, ts, _ = state.split(":", 2)
    future = float(ts) + gh.MAX_STATE_AGE_SEC + 1
    with pytest.raises(gh.GitHubOAuthError):
        gh.verify_state(state=state, secret="s3cret", now=future)


def test_build_authorize_url_includes_client_id_redirect_scope_state():
    url = gh.build_authorize_url(
        client_id="cid", redirect_uri="http://localhost:18100/cb", state="u_jane:123:abc",
    )
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=cid" in url
    assert "state=u_jane%3A123%3Aabc" in url
    assert "scope=repo" in url


def test_exchange_code_returns_token(monkeypatch):
    class _Resp:
        def read(self): return json.dumps({"access_token": "ghu_abc123", "token_type": "bearer"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(gh.urllib.request, "urlopen", lambda req, timeout=10: _Resp())
    token = gh.exchange_code(client_id="cid", client_secret="csecret", redirect_uri="http://x/cb", code="the-code")
    assert token == "ghu_abc123"


def test_exchange_code_github_error_raises(monkeypatch):
    class _Resp:
        def read(self): return json.dumps({"error": "bad_verification_code", "error_description": "expired"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(gh.urllib.request, "urlopen", lambda req, timeout=10: _Resp())
    with pytest.raises(gh.GitHubOAuthError, match="expired"):
        gh.exchange_code(client_id="cid", client_secret="csecret", redirect_uri="http://x/cb", code="bad-code")


def test_exchange_code_network_failure_raises(monkeypatch):
    def _boom(req, timeout=10):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(gh.urllib.request, "urlopen", _boom)
    with pytest.raises(gh.GitHubOAuthError):
        gh.exchange_code(client_id="cid", client_secret="csecret", redirect_uri="http://x/cb", code="the-code")
