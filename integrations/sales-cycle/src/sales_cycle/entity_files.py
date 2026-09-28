"""Reads "feature request" notes straight from disk, where Vexa keeps every customer's files.

We read the files directly rather than asking Vexa's web API for them, because those customer
folders aren't tied to a real logged-in account with its own login key — they're just folders Vexa
made up on the fly per customer. Reading the files ourselves sidesteps that entirely.

Each feature-request note is a small text file shaped like this:
    ---
    type: feature_request
    id: some-short-id
    title: A human-readable title
    ---
    The body text describing the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class FeatureRequestEntity:
    path: Path
    workspace_id: str
    title: str
    body: str


def _parse_frontmatter(text: str) -> tuple[dict, str] | None:
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    _, fm_text, body = parts
    fm = yaml.safe_load(fm_text) or {}
    if not isinstance(fm, dict):
        return None
    return fm, body.strip()


def iter_feature_request_paths(workspaces_root: Path) -> list[tuple[Path, str]]:
    """Every feature-request note's location, from every customer's folder — just a directory
    listing, no file reads. Feature-request notes pile up forever (never deleted), so keeping this
    step read-free lets a caller skip the ones it's already seen before paying for the file read +
    YAML parse in `parse_feature_request_file` below."""
    out: list[tuple[Path, str]] = []
    if not workspaces_root.is_dir():
        return out
    for workspace_dir in workspaces_root.iterdir():
        if not workspace_dir.is_dir() or workspace_dir.name.startswith("."):
            continue
        fr_dir = workspace_dir / "kg" / "entities" / "feature_request"
        if not fr_dir.is_dir():
            continue
        out.extend((path, workspace_dir.name) for path in sorted(fr_dir.glob("*.md")))
    return out


def parse_feature_request_file(path: Path, workspace_id: str) -> FeatureRequestEntity | None:
    """Reads and parses one note. `None` if it's missing, unreadable, or not a valid entity file."""
    try:
        parsed = _parse_frontmatter(path.read_text())
    except (OSError, yaml.YAMLError):
        return None
    if parsed is None:
        return None
    fm, body = parsed
    title = fm.get("title") or fm.get("id") or path.stem
    return FeatureRequestEntity(path=path, workspace_id=workspace_id, title=str(title), body=body)
