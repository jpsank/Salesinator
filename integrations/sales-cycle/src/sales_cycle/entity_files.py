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


def find_feature_request_entities(workspaces_root: Path) -> list[FeatureRequestEntity]:
    """Every feature-request note, from every customer's folder. Whoever calls this decides which
    ones are actually new (see store.py's `seen_files`) — this function just returns all of them."""
    out: list[FeatureRequestEntity] = []
    if not workspaces_root.is_dir():
        return out
    for workspace_dir in workspaces_root.iterdir():
        if not workspace_dir.is_dir() or workspace_dir.name.startswith("."):
            continue
        fr_dir = workspace_dir / "kg" / "entities" / "feature_request"
        if not fr_dir.is_dir():
            continue
        for path in sorted(fr_dir.glob("*.md")):
            try:
                parsed = _parse_frontmatter(path.read_text())
            except (OSError, yaml.YAMLError):
                continue
            if parsed is None:
                continue
            fm, body = parsed
            title = fm.get("title") or fm.get("id") or path.stem
            out.append(FeatureRequestEntity(
                path=path, workspace_id=workspace_dir.name, title=str(title), body=body,
            ))
    return out
