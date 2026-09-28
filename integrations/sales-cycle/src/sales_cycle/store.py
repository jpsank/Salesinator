"""A tiny local database this add-on keeps for itself — just a plain file on disk, nothing fancy or
external to install. It tracks two things:

- `seen_requests`: which live feature-request cards we've already posted to Slack, so a copilot that
  re-surfaces the same request (or a watcher reconnect replaying its recent backlog) never posts it
  twice. Keyed by a synthetic `live:<meeting_id>:<title>` string, not a file path — nothing here reads
  from disk.
- `pending_approvals`: each feature request's progress. Starts as "pending", becomes "approved" once
  someone reacts ✅ in Slack, "dispatched" once the AI agent starts building it, and "done" once
  the finished branch is pushed to GitHub.
- `oauth_connections`: one row per external service (HubSpot, Slack, ...) connected via the
  "Connect X" button in Vexa's Settings page — one shared connection for the whole team, not
  per-rep, so this is keyed by `provider` name alone.
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS oauth_connections (
                    provider TEXT PRIMARY KEY,
                    access_token TEXT NOT NULL, refresh_token TEXT,
                    expires_at REAL, account_label TEXT,
                    connected_at REAL NOT NULL
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
        with self._conn() as conn:
            conn.execute(
                "UPDATE pending_approvals SET status = 'dispatched', branch = ?, workload_id = ? WHERE id = ?",
                (branch, workload_id, approval_id),
            )

    def list_dispatched_unpushed(self) -> list[PendingApproval]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_approvals WHERE status = 'dispatched'").fetchall()
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
