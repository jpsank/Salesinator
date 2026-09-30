"""A tiny local database this add-on keeps for itself — just a plain file on disk, nothing fancy or
external to install. It tracks four things:

- `seen_requests`: which live feature-request cards we've already posted to Slack, so a copilot that
  re-surfaces the same request (or a watcher reconnect replaying its recent backlog) never posts it
  twice. Keyed by a synthetic `live:<meeting_id>:<title>` string, not a file path — nothing here reads
  from disk.
- `pending_approvals`: each feature request's progress. Starts as "pending", becomes "approved" once
  someone reacts ✅ in Slack, "dispatched" once the AI agent starts building it, "pushed" once the
  finished branch reaches GitHub, and "done" once a pull request is open for it. A "dispatched" row
  that sits too long with no push (a crashed/hung turn) reverts to "approved" for one retry, then
  "failed" for good — see `fail_or_retry_stale_dispatch`. Retrying starts a fresh turn (a new
  worktree, a new workload_id); the stale turn's own worktree is simply abandoned, not actively
  reclaimed — a known, accepted gap, not something this store tracks or cleans up.
- `oauth_connections`: one row per external service (HubSpot, Slack, ...) connected via the
  "Connect X" button in Vexa's Settings page — one shared connection for the whole team, not
  per-rep, so this is keyed by `provider` name alone.
- `active_watchers`: which meetings currently SHOULD have a live card watcher running — a durable
  record surviving a process restart, unlike the in-process asyncio.Task tracking it exists
  alongside. A meeting still genuinely live loses its watcher silently the moment this service
  restarts mid-call; a periodic sweep (api.py's sweep_live_watchers) checks this table against the
  live task set and restarts anything missing. Removed once the meeting reaches a terminal state.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OAuthConnection:
    provider: str
    access_token: str
    refresh_token: str | None
    expires_at: float | None  # unix seconds; None = doesn't expire
    account_label: str | None  # e.g. the connected HubSpot account's domain, for display
    connected_at: float


@dataclass(frozen=True)
class PendingApproval:
    id: int
    slack_channel: str
    slack_ts: str
    workspace_id: str
    source_key: str
    title: str
    body: str
    status: str
    branch: str | None
    workload_id: str | None
    created_at: float
    dispatched_at: float | None
    dispatch_attempts: int


class Store:
    def __init__(self, db_path: str):
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        # We keep this one connection open for as long as the Store exists, instead of opening a new
        # one every time. That matters most for tests using an in-memory database — opening a new
        # in-memory database each time would silently start over from empty every call.
        self._connection = sqlite3.connect(self._db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._init()

    @contextmanager
    def _conn(self):
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_requests (
                    key TEXT PRIMARY KEY, seen_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slack_channel TEXT NOT NULL, slack_ts TEXT NOT NULL,
                    workspace_id TEXT NOT NULL, source_key TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    branch TEXT, workload_id TEXT,
                    created_at REAL NOT NULL,
                    UNIQUE(slack_channel, slack_ts)
                )
            """)
            # Migration: dispatched_at/dispatch_attempts, added for the stale-dispatch timeout sweep
            # (a turn that crashes or hangs must not stay 'dispatched' forever). sqlite has no
            # `ADD COLUMN IF NOT EXISTS` — guard against re-running on an already-migrated DB.
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_approvals)").fetchall()}
            if "dispatched_at" not in existing_cols:
                conn.execute("ALTER TABLE pending_approvals ADD COLUMN dispatched_at REAL")
            if "dispatch_attempts" not in existing_cols:
                conn.execute(
                    "ALTER TABLE pending_approvals ADD COLUMN dispatch_attempts INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_connections (
                    provider TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL, refresh_token TEXT,
                    expires_at REAL, account_label TEXT,
                    connected_at REAL NOT NULL
                )
            """)
            # DURABLE record of "this meeting should have a live card watcher running" — the
            # in-process asyncio.Task tracking (_watch_meeting_tasks) doesn't survive a process
            # restart, so a meeting still genuinely live loses its watcher silently the moment this
            # service restarts mid-call (reproduced live: exactly this, tonight). This row is the
            # thing a sweep checks against to notice and restart a missed one — see
            # api.py's sweep_live_watchers().
            conn.execute("""
                CREATE TABLE IF NOT EXISTS active_watchers (
                    meeting_id TEXT PRIMARY KEY, subject TEXT NOT NULL, started_at REAL NOT NULL
                )
            """)

    def is_seen(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM seen_requests WHERE key = ?", (key,)).fetchone()
        return row is not None

    def mark_seen(self, key: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_requests (key, seen_at) VALUES (?, ?)", (key, time.time())
            )

    def record_watcher_started(self, meeting_id: str, subject: str) -> None:
        """Idempotent — a restart re-registering the SAME meeting_id just refreshes started_at,
        never duplicates a row (meeting_id is the primary key)."""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_watchers (meeting_id, subject, started_at) VALUES (?, ?, ?)",
                (meeting_id, subject, time.time()),
            )

    def record_watcher_stopped(self, meeting_id: str) -> None:
        """The meeting reached a terminal state (or the sweep confirmed it no longer exists) — no
        longer needs watching. Safe to call even if no row exists."""
        with self._conn() as conn:
            conn.execute("DELETE FROM active_watchers WHERE meeting_id = ?", (meeting_id,))

    def list_active_watchers(self) -> list[tuple[str, str]]:
        """Every meeting this process believes should currently have a live card watcher running —
        checked against the IN-PROCESS task set by the sweep, since this row surviving a restart is
        exactly the point (see the table's own comment)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT meeting_id, subject FROM active_watchers").fetchall()
        return [(r["meeting_id"], r["subject"]) for r in rows]

    def record_pending_approval(
        self, *, slack_channel: str, slack_ts: str, workspace_id: str, source_key: str,
        title: str, body: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pending_approvals "
                "(slack_channel, slack_ts, workspace_id, source_key, title, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slack_channel, slack_ts, workspace_id, source_key, title, body, time.time()),
            )

    def approve(self, *, slack_channel: str, slack_ts: str) -> PendingApproval | None:
        """Idempotent: a second ✅ (or a reaction on an already-approved message) is a no-op, not an error."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE slack_channel = ? AND slack_ts = ? AND status = 'pending'",
                (slack_channel, slack_ts),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE pending_approvals SET status = 'approved' WHERE id = ?", (row["id"],))
        return PendingApproval(**{**dict(row), "status": "approved"})

    def list_approved_unprocessed(self) -> list[PendingApproval]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_approvals WHERE status = 'approved'").fetchall()
        return [PendingApproval(**dict(r)) for r in rows]

    def claim_for_dispatch(self, approval_id: int) -> bool:
        """Atomically moves 'approved' → 'dispatching', so the real-time path (fires on the Slack ✅)
        and the cron sweep can never both start an implementation turn for the same request — whichever
        gets here first wins the `WHERE status = 'approved'`, the other gets `rowcount == 0`."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE pending_approvals SET status = 'dispatching' WHERE id = ? AND status = 'approved'",
                (approval_id,),
            )
            return cur.rowcount == 1

    def revert_to_approved(self, approval_id: int) -> None:
        """Undoes a claim whose dispatch attempt failed, so the cron sweep retries it later instead
        of leaving it stuck in 'dispatching' forever."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_approvals SET status = 'approved' WHERE id = ? AND status = 'dispatching'",
                (approval_id,),
            )

    def mark_dispatched(self, approval_id: int, *, branch: str, workload_id: str | None) -> None:
        """`dispatch_attempts` counts EVERY turn started for this approval, including a prior one
        that later timed out — `fail_or_retry_stale_dispatch` reads it to decide retry vs. give up."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_approvals SET status = 'dispatched', branch = ?, workload_id = ?, "
                "dispatched_at = ?, dispatch_attempts = dispatch_attempts + 1 WHERE id = ?",
                (branch, workload_id, time.time(), approval_id),
            )

    def list_dispatched_unpushed(self) -> list[PendingApproval]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_approvals WHERE status = 'dispatched'").fetchall()
        return [PendingApproval(**dict(r)) for r in rows]

    def fail_or_retry_stale_dispatch(self, approval_id: int, *, max_attempts: int) -> str:
        """A 'dispatched' approval has sat too long with no push — the turn likely crashed or hung.
        Under `max_attempts`: revert to 'approved' so the next sweep starts a FRESH turn (a new
        worktree, a new workload_id — the stale one is simply abandoned, not actively cleaned up;
        see the module docstring's note on that). At `max_attempts`: give up for good, 'failed', so
        a structurally-broken request can't silently re-spin an AI turn against the real repo
        forever. Returns "retried", "failed", or "skipped" (the row already moved on — e.g. a
        concurrent push-check in the same sweep beat this check to it; not an error, just a race)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT dispatch_attempts FROM pending_approvals WHERE id = ? AND status = 'dispatched'",
                (approval_id,),
            ).fetchone()
            if row is None:
                return "skipped"
            if row["dispatch_attempts"] >= max_attempts:
                conn.execute(
                    "UPDATE pending_approvals SET status = 'failed' WHERE id = ? AND status = 'dispatched'",
                    (approval_id,),
                )
                return "failed"
            conn.execute(
                "UPDATE pending_approvals SET status = 'approved' WHERE id = ? AND status = 'dispatched'",
                (approval_id,),
            )
            return "retried"

    def mark_pushed(self, approval_id: int) -> None:
        """The branch reached GitHub — but a pull request isn't open for it yet (see
        `list_pushed_unopened`). Deliberately a separate state from 'done': a re-fetched git state on
        a LATER sweep can no longer prove the push happened (the isolated worktree it was pushed from
        is already released), so PR-open retries are driven by this store state, not by re-checking
        git."""
        with self._conn() as conn:
            conn.execute("UPDATE pending_approvals SET status = 'pushed' WHERE id = ?", (approval_id,))

    def list_pushed_unopened(self) -> list[PendingApproval]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_approvals WHERE status = 'pushed'").fetchall()
        return [PendingApproval(**dict(r)) for r in rows]

    def mark_done(self, approval_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE pending_approvals SET status = 'done' WHERE id = ?", (approval_id,))

    def get_oauth_connection(self, provider: str) -> OAuthConnection | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM oauth_connections WHERE provider = ?", (provider,)
            ).fetchone()
        return OAuthConnection(**dict(row)) if row is not None else None

    def save_oauth_connection(
        self, *, provider: str, access_token: str, refresh_token: str | None,
        expires_at: float | None, account_label: str | None,
    ) -> None:
        """Replaces any existing connection for this provider — reconnecting (or a token refresh)
        always wins over whatever was there before."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO oauth_connections "
                "(provider, access_token, refresh_token, expires_at, account_label, connected_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider) DO UPDATE SET "
                "access_token = excluded.access_token, refresh_token = excluded.refresh_token, "
                "expires_at = excluded.expires_at, account_label = excluded.account_label",
                (provider, access_token, refresh_token, expires_at, account_label, time.time()),
            )

    def disconnect_oauth(self, provider: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM oauth_connections WHERE provider = ?", (provider,))
