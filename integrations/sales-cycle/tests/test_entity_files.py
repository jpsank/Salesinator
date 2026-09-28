from pathlib import Path

from sales_cycle.entity_files import iter_feature_request_paths, parse_feature_request_file


def _write(root: Path, subject: str, slug: str, title: str, body: str) -> Path:
    d = root / subject / "kg" / "entities" / "feature_request"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(f"---\ntype: feature_request\nid: {slug}\ntitle: {title}\n---\n{body}\n")
    return p


def _find(root: Path):
    """Mirrors what poller.py does: list paths (cheap), then parse each one (the old
    find_feature_request_entities behavior, recombined for these tests)."""
    out = []
    for path, workspace_id in iter_feature_request_paths(root):
        entity = parse_feature_request_file(path, workspace_id)
        if entity is not None:
            out.append(entity)
    return out


def test_finds_entities_across_workspaces(tmp_path: Path):
    _write(tmp_path, "cust-1", "csv-export", "CSV export", "They want CSV export.")
    _write(tmp_path, "cust-2", "sso", "SSO login", "They need SAML SSO.")

    found = _find(tmp_path)
    titles = sorted((e.workspace_id, e.title) for e in found)
    assert titles == [("cust-1", "CSV export"), ("cust-2", "SSO login")]


def test_ignores_workspaces_with_no_feature_request_dir(tmp_path: Path):
    (tmp_path / "cust-3" / "kg" / "entities" / "person").mkdir(parents=True)
    (tmp_path / "cust-3" / "kg" / "entities" / "person" / "jane.md").write_text(
        "---\ntype: person\nid: jane\ntitle: Jane\n---\n"
    )
    assert _find(tmp_path) == []


def test_missing_root_returns_empty():
    assert iter_feature_request_paths(Path("/does/not/exist")) == []
    assert _find(Path("/does/not/exist")) == []


def test_malformed_file_is_skipped_not_fatal(tmp_path: Path):
    d = tmp_path / "cust-1" / "kg" / "entities" / "feature_request"
    d.mkdir(parents=True)
    (d / "broken.md").write_text("not frontmatter at all")
    _write(tmp_path, "cust-1", "good", "Good one", "body")

    found = _find(tmp_path)
    assert [e.title for e in found] == ["Good one"]


def test_ignores_dotdirs(tmp_path: Path):
    (tmp_path / ".system").mkdir()
    _write(tmp_path, "cust-1", "x", "X", "y")
    found = _find(tmp_path)
    assert [e.workspace_id for e in found] == ["cust-1"]


def test_iter_paths_does_not_read_file_contents(tmp_path: Path, monkeypatch):
    """The whole point of splitting list-vs-parse: listing must never touch file contents."""
    _write(tmp_path, "cust-1", "x", "X", "y")

    def _boom(*a, **k):
        raise AssertionError("iter_feature_request_paths must not read file contents")

    monkeypatch.setattr(Path, "read_text", _boom)
    paths = iter_feature_request_paths(tmp_path)
    assert len(paths) == 1


def test_parse_missing_file_returns_none(tmp_path: Path):
    assert parse_feature_request_file(tmp_path / "nope.md", "cust-1") is None
