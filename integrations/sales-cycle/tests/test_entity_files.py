from pathlib import Path

from sales_cycle.entity_files import find_feature_request_entities


def _write(root: Path, subject: str, slug: str, title: str, body: str) -> Path:
    d = root / subject / "kg" / "entities" / "feature_request"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(f"---\ntype: feature_request\nid: {slug}\ntitle: {title}\n---\n{body}\n")
    return p


def test_finds_entities_across_workspaces(tmp_path: Path):
    _write(tmp_path, "cust-1", "csv-export", "CSV export", "They want CSV export.")
    _write(tmp_path, "cust-2", "sso", "SSO login", "They need SAML SSO.")

    found = find_feature_request_entities(tmp_path)
    titles = sorted((e.workspace_id, e.title) for e in found)
    assert titles == [("cust-1", "CSV export"), ("cust-2", "SSO login")]


def test_ignores_workspaces_with_no_feature_request_dir(tmp_path: Path):
    (tmp_path / "cust-3" / "kg" / "entities" / "person").mkdir(parents=True)
    (tmp_path / "cust-3" / "kg" / "entities" / "person" / "jane.md").write_text(
        "---\ntype: person\nid: jane\ntitle: Jane\n---\n"
    )
    assert find_feature_request_entities(tmp_path) == []


def test_missing_root_returns_empty(tmp_path: Path):
    assert find_feature_request_entities(tmp_path / "does-not-exist") == []


def test_malformed_file_is_skipped_not_fatal(tmp_path: Path):
    d = tmp_path / "cust-1" / "kg" / "entities" / "feature_request"
    d.mkdir(parents=True)
    (d / "broken.md").write_text("not frontmatter at all")
    _write(tmp_path, "cust-1", "good", "Good one", "body")

    found = find_feature_request_entities(tmp_path)
    assert [e.title for e in found] == ["Good one"]


def test_ignores_dotdirs(tmp_path: Path):
    (tmp_path / ".system").mkdir()
    _write(tmp_path, "cust-1", "x", "X", "y")
    found = find_feature_request_entities(tmp_path)
    assert [e.workspace_id for e in found] == ["cust-1"]
