"""workspace_publish — publish a vexa-born workspace to GitHub (counterpart of attach/swap).

Proves the publish lifecycle on REAL git over a LOCAL bare repo as the push target (no network;
the GitHub creation call is an injected fake, mirroring how workspace_attach tests inject CloneFn):
  create+push full history → re-publish is a plain push → divergence fails loud (no force push) →
  attached workspaces are refused → tokens never persist and never leak into errors (P15).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from control_plane.workspace_attach import swap_workspace
from control_plane.workspace_publish import (
    PUBLISH_REMOTE,
    PublishError,
    PullRequestError,
    RepoExistsError,
    create_pull_request,
    owner_repo_from_url,
    publish_workspace,
    published_remote_url,
)

TOKEN = "ghp_SECRET_token_123"


def _run(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _workspace(root: Path, subject: str, commits: int = 2) -> Path:
    """A vexa-born (seeded-style) active workspace with real history at <root>/<subject>."""
    ws = root / subject
    ws.mkdir(parents=True)
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t")
    _run(ws, "config", "user.name", "t")
    for i in range(commits):
        (ws / "CLAUDE.md").write_text(f"root v{i}\n")
        _run(ws, "add", "-A")
        _run(ws, "commit", "-q", "-m", f"c{i}")
    return ws


def _bare(path: Path) -> Path:
    path.mkdir(parents=True)
    _run(path, "init", "-q", "--bare", "-b", "main")
    return path


@pytest.fixture(autouse=True)
def _local_push_targets_allowed(tmp_path, monkeypatch):
    """This file pushes to a LOCAL bare repo so the suite never touches the network. A scheme-less
    path is refused as a push target by default — it is a location on the SERVER, and a caller who
    can name one can make this process carry their PAT to it (see control_plane/repo_ref). The
    self-host opt-in is what makes the local-bare-repo fixture legal; every test here inherits it."""
    monkeypatch.setenv("VEXA_ALLOW_LOCAL_REPO_ROOT", str(tmp_path))


def test_publish_creates_repo_and_pushes_full_history(tmp_path):
    """The create path: the injected creator is called with the caller's args and its returned URL is
    pushed to — full history, head sha returned, repo_url token-free."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1", commits=3)
    bare = _bare(tmp_path / "remote.git")
    calls: list[tuple] = []

    def fake_create(name, private, token, org):
        calls.append((name, private, token, org))
        return str(bare)

    res = publish_workspace(root, "u1", token=TOKEN, repo_name="my-workspace",
                            private=True, create_repo=fake_create)

    assert calls == [("my-workspace", True, TOKEN, None)]
    assert res.created is True and res.pushed_ref == "main"
    assert res.head_sha == _run(ws, "rev-parse", "HEAD")
    # FULL history landed on the remote
    assert _run(bare, "rev-parse", "main") == res.head_sha
    assert _run(bare, "rev-list", "--count", "main") == "3"


def test_publish_to_remote_url_skips_creation(tmp_path):
    """remote_url given → no creation call, plain push to the pre-created (empty) repo."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1")
    bare = _bare(tmp_path / "pre.git")

    def never_create(*a):  # pragma: no cover - must not run
        raise AssertionError("create_repo must not be called when remote_url is given")

    res = publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare), create_repo=never_create)
    assert res.created is False
    assert _run(bare, "rev-parse", "main") == res.head_sha


def test_republish_same_remote_is_plain_push(tmp_path):
    """Idempotent-ish: publish, commit more, publish again to the same remote — a fast-forward push."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    bare = _bare(tmp_path / "remote.git")
    publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))

    (ws / "more.md").write_text("more\n")
    _run(ws, "add", "-A")
    _run(ws, "commit", "-q", "-m", "more")

    res = publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))
    assert _run(bare, "rev-parse", "main") == res.head_sha == _run(ws, "rev-parse", "HEAD")


def test_divergence_fails_loud_never_force(tmp_path):
    """The remote grew history the workspace doesn't have → a clear error; the remote's commit
    survives (NO force push, ever)."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1")
    bare = _bare(tmp_path / "remote.git")
    # seed the remote with foreign history
    other = tmp_path / "other"
    other.mkdir()
    _run(other, "init", "-q", "-b", "main")
    _run(other, "config", "user.email", "o@o")
    _run(other, "config", "user.name", "o")
    (other / "X").write_text("foreign\n")
    _run(other, "add", "-A")
    _run(other, "commit", "-q", "-m", "foreign")
    _run(other, "push", "-q", str(bare), "main")
    foreign_sha = _run(bare, "rev-parse", "main")

    with pytest.raises(PublishError):
        publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))
    assert _run(bare, "rev-parse", "main") == foreign_sha  # remote untouched


def test_token_never_persisted_and_errors_redacted(tmp_path):
    """P15: after a publish the workspace's git config carries NO token; the dedicated remote exists
    token-free and origin was never touched; a push failure's message is token-redacted."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    # A local path, not a real remote host: origin being a LOCAL path (the opted-in
    # VEXA_ALLOW_LOCAL_REPO_ROOT self-host case) is exactly what makes publish proceed rather than
    # refuse (see test_attached_workspace_is_refused for the refusal side, where origin is a real
    # remote host instead) — this fixture stands in for that self-host case, not an external clone.
    keep = tmp_path / "keep"
    _run(ws, "remote", "add", "origin", str(keep))
    bare = _bare(tmp_path / "remote.git")

    publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))

    cfg = (ws / ".git" / "config").read_text()
    assert TOKEN not in cfg
    assert _run(ws, "remote", "get-url", "origin") == str(keep)
    assert _run(ws, "remote", "get-url", PUBLISH_REMOTE) == str(bare)

    # a failing push (bogus remote) surfaces a token-free error
    with pytest.raises(PublishError) as ei:
        publish_workspace(root, "u1", token=TOKEN, remote_url=str(tmp_path / "nope.git"))
    assert TOKEN not in str(ei.value)


def test_create_failure_errors_are_token_free(tmp_path):
    """Creator failures (already-exists and generic) surface redacted, actionable errors."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1")

    def exists(*a):
        raise RepoExistsError("a repository named 'w' already exists under your account — pick "
                              "another name, or pass its URL as remote_url to push into it")

    with pytest.raises(RepoExistsError) as ei:
        publish_workspace(root, "u1", token=TOKEN, repo_name="w", create_repo=exists)
    assert "already exists" in str(ei.value) and TOKEN not in str(ei.value)


def test_attached_workspace_is_refused(tmp_path):
    """Vexa-born only: an ATTACHED external (remote) repo already has a home — publish refuses it.
    The origin used for the clone here is a local tmp_path (network-free test setup, same as every
    other test in this file), but the origin's URL is REWRITTEN to a remote-shaped one afterward —
    a local-path origin is deliberately NOT refused (see
    test_local_origin_workspace_is_not_refused), so exercising the real "external repo" case needs
    a remote-shaped host, not just a local clone source."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    origin = tmp_path / "external"
    origin.mkdir()
    _run(origin, "init", "-q", "-b", "main")
    _run(origin, "config", "user.email", "t@t")
    _run(origin, "config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("CUSTOM ROOT")
    _run(origin, "add", "-A")
    _run(origin, "commit", "-q", "-m", "seed")
    swap_workspace(root, "u1", str(origin), "main")   # active workspace is now the attached repo
    _run(ws, "remote", "set-url", "origin", "https://github.com/acme/external.git")

    with pytest.raises(PublishError) as ei:
        publish_workspace(root, "u1", token=TOKEN, repo_name="w",
                          create_repo=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert "attached" in str(ei.value)


def test_local_origin_workspace_is_not_refused(tmp_path):
    """A workspace attached from a LOCAL PATH (the self-host VEXA_ALLOW_LOCAL_REPO_ROOT escape
    hatch — see control_plane/repo_ref.py) isn't a GitHub home at all, so publish may add
    `vexa-publish` to it additively — origin (the local path) is left untouched."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    origin = tmp_path / "external"
    origin.mkdir()
    _run(origin, "init", "-q", "-b", "main")
    _run(origin, "config", "user.email", "t@t")
    _run(origin, "config", "user.name", "t")
    (origin / "CLAUDE.md").write_text("CUSTOM ROOT")
    _run(origin, "add", "-A")
    _run(origin, "commit", "-q", "-m", "seed")
    swap_workspace(root, "u1", str(origin), "main")   # origin stays a local path — not rewritten

    result = publish_workspace(root, "u1", token=TOKEN, remote_url=str(_bare(tmp_path / "remote.git")))
    assert result.created is False
    assert _run(ws, "remote", "get-url", "origin") == str(origin)   # origin untouched


def test_bad_inputs_are_value_errors(tmp_path):
    """Missing token / bad repo_name / no workspace / no commits fail loud with clear messages."""
    root = tmp_path / "workspaces"
    with pytest.raises(ValueError):
        publish_workspace(root, "u1", token="  ", repo_name="w")   # no token
    with pytest.raises(PublishError):
        publish_workspace(root, "u1", token=TOKEN, repo_name="w")  # no workspace yet
    _workspace(root, "u2")
    with pytest.raises(ValueError):
        publish_workspace(root, "u2", token=TOKEN, repo_name="bad name!")  # invalid repo name
    with pytest.raises(ValueError):
        publish_workspace(root, "u2", token=TOKEN)  # neither repo_name nor remote_url

# ── published_remote_url — the read-side probe the terminal renders the published state from ────────


def test_published_remote_url_reflects_publish_state(tmp_path):
    """None before a publish; the token-free remote URL (``.git`` stripped, like PublishResult) after."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    assert published_remote_url(ws) is None                     # never published

    bare = _bare(tmp_path / "remote.git")
    publish_workspace(root, "u1", token=TOKEN, remote_url=str(bare))

    url = published_remote_url(ws)
    assert url == str(bare)[: -len(".git")]                     # the display URL of the publish remote
    assert TOKEN not in url                                     # P15: never a credential in the read path


def test_published_remote_url_strips_embedded_credentials(tmp_path):
    """Defense in depth (P15): even a credential somehow persisted in the remote URL never reaches the
    client — user:token@ is stripped, and the URL is the human (no ``.git``) form."""
    root = tmp_path / "workspaces"
    ws = _workspace(root, "u1")
    _run(ws, "remote", "add", PUBLISH_REMOTE, f"https://x-access-token:{TOKEN}@github.com/u/repo.git")
    assert published_remote_url(ws) == "https://github.com/u/repo"


def test_published_remote_url_quiet_on_non_repo(tmp_path):
    """A state probe, not an operation: a missing dir / non-repo is simply 'not published' (None)."""
    assert published_remote_url(tmp_path / "nope") is None
    plain = tmp_path / "plain"
    plain.mkdir()
    assert published_remote_url(plain) is None


def test_publish_ws_dir_targets_explicit_workspace(tmp_path):
    """`ws_dir` publishes THAT workspace (an own parked slot / shared dir the API resolved), not the
    subject's seed dir — the slug-aware endpoint path."""
    root = tmp_path / "workspaces"
    _workspace(root, "u1", commits=1)                       # the seed dir — must NOT be pushed
    other = _workspace(root / ".attached" / "u1", "acme-1", commits=2)
    (other / "kg").mkdir(); (other / "kg" / "x.md").write_text("acme\n")
    _run(other, "add", "-A"); _run(other, "commit", "-q", "-m", "acme content")
    bare = _bare(tmp_path / "remote.git")

    res = publish_workspace(root, "u1", token=TOKEN, repo_name="acme",
                            create_repo=lambda n, p, t, o: str(bare), ws_dir=other)
    assert res.created is True
    assert _run(bare, "rev-parse", "main") == _run(other, "rev-parse", "HEAD")
    assert published_remote_url(other)                       # the explicit dir carries the publish remote
    assert published_remote_url(root / "u1") is None         # the seed dir was untouched


def test_publish_ws_dir_refuses_attached_clone(tmp_path):
    """An explicit target with an `origin` remote is an ATTACHED external clone — refused (its home is
    that repo; publish is for vexa-born workspaces)."""
    root = tmp_path / "workspaces"
    other = _workspace(root / ".attached" / "u1", "clone-1", commits=1)
    _run(other, "remote", "add", "origin", "https://github.com/me/upstream.git")
    with pytest.raises(PublishError, match="attached from an external repo"):
        publish_workspace(root, "u1", token=TOKEN, repo_name="x",
                          create_repo=lambda n, p, t, o: "unused", ws_dir=other)


# ── owner_repo_from_url ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,owner,repo", [
    ("https://github.com/jpsank/vexa-sales-cycle.git", "jpsank", "vexa-sales-cycle"),
    ("https://github.com/jpsank/vexa-sales-cycle", "jpsank", "vexa-sales-cycle"),
    ("git@github.com:jpsank/vexa-sales-cycle.git", "jpsank", "vexa-sales-cycle"),
])
def test_owner_repo_from_url_parses_https_and_ssh(url, owner, repo):
    assert owner_repo_from_url(url) == (owner, repo)


def test_owner_repo_from_url_rejects_garbage():
    with pytest.raises(ValueError):
        owner_repo_from_url("not a url at all")


# ── create_pull_request ─────────────────────────────────────────────────────────

def _attached_workspace_with_origin(root: Path, subject: str, remote_url: str) -> Path:
    ws = root / subject
    ws.mkdir(parents=True)
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
    (ws / "f.txt").write_text("x\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "c0")
    _run(ws, "remote", "add", "origin", remote_url)
    return ws


def test_create_pull_request_calls_github_with_the_current_branch(tmp_path):
    ws = _attached_workspace_with_origin(tmp_path, "u1", "https://github.com/acme/product.git")
    _run(ws, "checkout", "-q", "-b", "feature/csv-export")

    calls = []

    def fake_create_pr(owner, repo, *, head, base, title, body, token):
        calls.append((owner, repo, head, base, title, body, token))
        return {"url": "https://github.com/acme/product/pull/7", "number": 7}

    result = create_pull_request(
        ws, title="CSV export", body="customer wants it", base="main", token=TOKEN, create_pr=fake_create_pr,
    )

    assert result == {"url": "https://github.com/acme/product/pull/7", "number": 7}
    assert calls == [("acme", "product", "feature/csv-export", "main", "CSV export", "customer wants it", TOKEN)]


def test_create_pull_request_refuses_with_no_home_remote(tmp_path):
    ws = tmp_path / "u1"
    ws.mkdir()
    _run(ws, "init", "-q", "-b", "main")
    _run(ws, "config", "user.email", "t@t"); _run(ws, "config", "user.name", "t")
    (ws / "f.txt").write_text("x\n"); _run(ws, "add", "-A"); _run(ws, "commit", "-q", "-m", "c0")

    with pytest.raises(PublishError, match="no GitHub home"):
        create_pull_request(ws, title="x", body="y", base="main", token=TOKEN, create_pr=lambda **k: {})


def test_create_pull_request_surfaces_github_errors_token_redacted(tmp_path):
    ws = _attached_workspace_with_origin(tmp_path, "u1", "https://github.com/acme/product.git")

    def failing_create_pr(owner, repo, **kwargs):
        raise PullRequestError("GitHub pull-request creation failed (HTTP 422): branch already has a pull request")

    with pytest.raises(PullRequestError, match="HTTP 422"):
        create_pull_request(ws, title="x", body="y", base="main", token=TOKEN, create_pr=failing_create_pr)
