"""A tiny local database this add-on keeps for itself — just a plain file on disk, nothing fancy or
external to install. It tracks two things:

- `seen_files`: which feature-request notes we've already posted to Slack, so we never post the
  same one twice.
- `pending_approvals`: each feature request's progress. Starts as "pending", becomes "approved" once
  someone reacts ✅ in Slack, "dispatched" once the AI agent starts building it, and "done" once
  the finished branch is pushed to GitHub.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PendingApproval:
    id: int
    slack_channel: str
    slack_ts: str
    workspace_id: str
    entity_path: str
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
                CREATE TABLE IF NOT EXISTS seen_files (
                    path TEXT PRIMARY KEY, seen_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slack_channel TEXT NOT NULL, slack_ts TEXT NOT NULL,
                    workspace_id TEXT NOT NULL, entity_path TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    branch TEXT, workload_id TEXT,
                    created_at REAL NOT NULL,
                    UNIQUE(slack_channel, slack_ts)
                )
            """)

    def is_seen(self, path: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM seen_files WHERE path = ?", (path,)).fetchone()
        return row is not None

    def mark_seen(self, path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_files (path, seen_at) VALUES (?, ?)", (path, time.time())
            )

    def record_pending_approval(
        self, *, slack_channel: str, slack_ts: str, workspace_id: str, entity_path: str,
        title: str, body: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pending_approvals "
                "(slack_channel, slack_ts, workspace_id, entity_path, title, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slack_channel, slack_ts, workspace_id, entity_path, title, body, time.time()),
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
