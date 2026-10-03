"""What did CI say about the agent's pull request?

The agent's output is only as good as its model, so it is checked by the repository's own CI (typecheck, tests, gates) on the pull request —
the verdict is read from GitHub and said once in the card's Slack thread, so a human sees "the agent's branch fails typecheck" before reading a
line of the diff. The decision (`verdict`) is pure and tested directly; `fetch_check_runs` is the one network call.

Public repositories need no token (GitHub's unauthenticated limit is about 60 calls an hour, so the sweep polls every few minutes). A repository
that cannot be read without a token is reported once as unreadable rather than guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sales_cycle._http import call

_PR_URL = re.compile(r"^https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)/?$")

# A run that ended in one of these means the pull request does not pass. `cancelled` (a superseded run), `skipped`, `neutral` and `stale` do not.
_FAILED = frozenset({"failure", "timed_out", "action_required", "startup_failure"})

NONE_AFTER_S = 8 * 60          # no check run this long after the pull request opened: nothing is running CI
GIVE_UP_AFTER_S = 60 * 60      # checks still running after this long: stop waiting


class GitHubError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PullRef:
    owner: str
    repo: str
    number: int

    @property
    def checks_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}/checks"


def parse_pr_url(url: str) -> PullRef | None:
    m = _PR_URL.match((url or "").strip())
    return PullRef(m.group(1), m.group(2), int(m.group(3))) if m else None


_CODE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


def workspace_globs(yaml_text: str) -> list[str]:
    """The `packages:` globs of a pnpm-workspace.yaml (the only part that matters here), read without a YAML dependency."""
    out: list[str] = []
    in_packages = False
    for raw in yaml_text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t", "-")):                       # a top-level key
            in_packages = line.strip() == "packages:"
            continue
        m = re.match(r"^\s*-\s*[\"']?([^\"'\s]+)[\"']?\s*$", line)
        if in_packages and m:
            out.append(m.group(1).strip("/"))
    return out


def _glob_regex(glob: str) -> re.Pattern:
    """A workspace glob names package DIRECTORIES: `core/meetings/services/*` is every directory one level down, and a file is covered when it
    lies inside one. `**` spans any depth."""
    body = re.escape(glob).replace(r"\*\*", ".+").replace(r"\*", "[^/]+")
    return re.compile(rf"^{body}/")


def uncovered_by_ci(changed_files: list[str], globs: list[str], standalone: list[str] | None = None) -> list[str]:
    """Changed TypeScript/JavaScript files that lie in no pnpm workspace package and in no standalone package CI tests on its own. CI's
    typecheck, build and test run over those packages only, so a green CI says nothing about the rest — the gap a pull request's thread should admit to."""
    if not globs:
        return []
    covered = [_glob_regex(g) for g in [*globs, *(standalone or [])]]
    return sorted(f for f in changed_files
                  if f.endswith(_CODE_SUFFIXES) and "node_modules/" not in f and not any(rx.match(f) for rx in covered))


# Upstream Vexa's process checks (contribution rights, merge card, PR value, welcome bot …): they are about how a contribution is documented, not
# whether the code works, and they fail on every agent pull request — counting them would report "CI failed" on every card.
DEFAULT_IGNORED_CHECKS = "merge-card,merge-card-comment,pr-value,pr-welcome,contribution-rights,contribution-rights-driver,comment,evaluate"


def ignored_checks(setting: str) -> frozenset[str]:
    return frozenset(n.strip() for n in setting.split(",") if n.strip())


def _areas(files: list[str], limit: int = 3) -> str:
    """The parts of the product some files belong to, in the repository's own terms: `packages/transcript-rendering`, `core/runtime`."""
    areas = sorted({"/".join(f.split("/")[:2]) for f in files})
    shown = ", ".join(f"`{a}`" for a in areas[:limit])
    return shown + (f" and {len(areas) - limit} more" if len(areas) > limit else "")


def verdict(runs: list[dict], *, age_s: float, none_after_s: float = NONE_AFTER_S, give_up_after_s: float = GIVE_UP_AFTER_S, checks_url: str = "",
            uncovered: list[str] | None = None, ignore: frozenset[str] = frozenset()) -> tuple[str, str] | None:
    """The final word on a draft's automatic checks, or None to keep waiting — worded for someone who does not read code: what happened, what it
    means, what to do, and a link to GitHub. ``runs`` are GitHub check runs (name, status, conclusion); ``age_s`` is how long ago the draft
    opened. Returns (state, text) with state one of passed, failed, none, timeout."""
    def link(label: str) -> str:
        return f" <{checks_url}|{label}>" if checks_url else ""
    runs = [r for r in runs if r.get("name") not in ignore]
    if not runs:
        if age_s >= none_after_s:
            return "none", (":grey_question: *No automatic checks ran on this draft*, so nobody has confirmed it works. A developer needs to review it before "
                            f"anything is used.{link('Open the draft on GitHub')}")
        return None
    if any(r.get("status") != "completed" for r in runs):
        if age_s >= give_up_after_s:
            return "timeout", (f":hourglass: *The automatic checks are taking unusually long* (over an hour), so there is no result yet.{link('Check on GitHub')}")
        return None
    if any(r.get("conclusion") in _FAILED for r in runs):
        return "failed", (":x: *This draft did not pass the automatic checks*, so it isn't ready to use. A developer needs to look at what failed."
                          f"{link('See what failed on GitHub')}")
    if uncovered:
        return "passed", (":white_check_mark: The automatic checks passed — *but they don't test the part of the product this change touches* "
                          f"({_areas(uncovered)}), so passing tells us little. A developer needs to read that code by hand before it's used.{link('Open the draft on GitHub')}")
    return "passed", f":white_check_mark: *The automatic checks passed on this draft.* A developer should still read it over before it's used.{link('Open the draft on GitHub')}"


def fetch_changed_files(ref: PullRef, *, timeout: float = 10.0, base: str = "https://api.github.com", limit: int = 300) -> list[str]:
    """The paths a pull request changes (up to ``limit``; GitHub pages them 100 at a time)."""
    out: list[str] = []
    for page in range(1, limit // 100 + 1):
        batch = _get(f"{base}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/files?per_page=100&page={page}", timeout).json()
        out += [f["filename"] for f in batch if f.get("filename")]
        if len(batch) < 100:
            break
    return out


def fetch_workspace_globs(ref: PullRef, sha: str, *, timeout: float = 10.0, base: str = "https://raw.githubusercontent.com") -> list[str]:
    """The pnpm workspace globs of the repository at ``sha``; empty when it has no pnpm-workspace.yaml."""
    try:
        text = _get(f"{base}/{ref.owner}/{ref.repo}/{sha}/pnpm-workspace.yaml", timeout).text
    except GitHubError as e:
        if e.status == 404:
            return []
        raise
    return workspace_globs(text)


def fetch_head_sha(ref: PullRef, *, timeout: float = 10.0, base: str = "https://api.github.com") -> str:
    resp = _get(f"{base}/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}", timeout)
    sha = (resp.json().get("head") or {}).get("sha")
    if not sha:
        raise GitHubError("the pull request has no head commit")
    return sha


def fetch_check_runs(ref: PullRef, sha: str, *, timeout: float = 10.0, base: str = "https://api.github.com") -> list[dict]:
    resp = _get(f"{base}/repos/{ref.owner}/{ref.repo}/commits/{sha}/check-runs?per_page=100", timeout)
    return [{"name": r.get("name"), "status": r.get("status"), "conclusion": r.get("conclusion")} for r in resp.json().get("check_runs") or []]


def _get(url: str, timeout: float):
    try:
        return call("GET", url, headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
                    timeout=timeout, error_cls=GitHubError, error_prefix="GitHub API")
    except GitHubError as e:
        m = re.search(r"\b(\d{3})\b", str(e))
        raise GitHubError(str(e), status=int(m.group(1)) if m else None) from e
