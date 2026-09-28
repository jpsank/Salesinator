"""Read `feature_request` kg entities off the shared agent-workspaces volume directly — no Vexa API
call, no auth mismatch (the copilot's `cust-<id>` subject isn't a real logged-in user with its own API
key, so the public per-user `/agent/workspace/file` route doesn't fit here; the workspace is just files
on a volume this service mounts read-only, so it reads them like any other consumer of the OKF bundle).

Entity file shape (docs/docs/how-to/workspace-files.mdx — the OKF bundle):
    ---
    type: feature_request
    id: <slug>
    title: <human title>
    ---
    <body>
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
    """Every `kg/entities/feature_request/*.md` under every `<workspaces_root>/<subject>/` — the caller
    (the poller) is the one that decides which of these are new via the seen-files store."""
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
