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
- `runtime_settings`: a plain key/value override for the handful of settings an operator can change
  from Vexa's Settings page instead of an env var + restart (first (and so far only) user:
  `slack_channel_id`). A key with no row here means "no override" — the caller falls back to its own
  env-var default, same override-wins-over-static-default shape `oauth_connections` already has for
  tokens (an OAuth-obtained token beats `SALES_CYCLE_*_TOKEN`).
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
    approved_at: float | None = None
    pushed_at: float | None = None
    done_at: float | None = None
    pr_url: str | None = None
    approved_by: str | None = None      # Slack user id(s) of the leader(s) whose ✅ approved it; None for the original single-✅ flow
    votes_up: int | None = None
    votes_down: int | None = None
    waiting_notified: int = 0
    last_error: str | None = None       # the latest reason pushing the branch / opening the PR failed; cleared when it succeeds


@dataclass(frozen=True)
class ZoomConnection:
    """One rep's authorized Zoom account, plus the Vexa API key (bot scope) used to send the bot AS that rep."""
    vexa_user_id: str
    zoom_user_id: str
    email: str | None
    access_token: str
    refresh_token: str
    expires_at: float
    vexa_token: str
    vexa_token_id: str | None
    connected_at: float


@dataclass(frozen=True)
class ZoomPending:
    nonce: str
    vexa_user_id: str
    vexa_token: str
    vexa_token_id: str | None
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
            # Migration: entity_path -> source_key. The column was renamed in code at some point
            # WITHOUT a matching migration for a database created under the old name — CREATE TABLE
            # IF NOT EXISTS only applies to a brand-new table, so any real, already-existing deployment
            # kept the stale column name forever, invisible until the first real INSERT actually hit
            # it. Reproduced live: a real feature_request card posted to Slack successfully, then
            # crashed on record_pending_approval with "no column named source_key" — and because nothing
            # marked it as posted before the crash, every restart re-posted the SAME card, over and
            # over, for as long as the meeting stayed live and the (otherwise correct) watcher
            # self-heal sweep kept restarting it.
            existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_approvals)").fetchall()}
            if "entity_path" in existing_cols and "source_key" not in existing_cols:
                conn.execute("ALTER TABLE pending_approvals RENAME COLUMN entity_path TO source_key")
                existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(pending_approvals)").fetchall()}
            # Migration: dispatched_at/dispatch_attempts, added for the stale-dispatch timeout sweep
            # (a turn that crashes or hangs must not stay 'dispatched' forever). sqlite has no
            # `ADD COLUMN IF NOT EXISTS` — guard against re-running on an already-migrated DB.
            if "dispatched_at" not in existing_cols:
                conn.execute("ALTER TABLE pending_approvals ADD COLUMN dispatched_at REAL")
            if "dispatch_attempts" not in existing_cols:
                conn.execute(
                    "ALTER TABLE pending_approvals ADD COLUMN dispatch_attempts INTEGER NOT NULL DEFAULT 0"
                )
            # Migration: the stage timestamps + PR link `pipeline_stats` reads. Requests that finished before
            # these columns existed keep NULLs, which the report simply leaves out of its timings.
            for column, ddl in (("approved_at", "REAL"), ("pushed_at", "REAL"), ("done_at", "REAL"), ("pr_url", "TEXT")):
                if column not in existing_cols:
                    conn.execute(f"ALTER TABLE pending_approvals ADD COLUMN {column} {ddl}")
            # Migration: who approved, and the 👍/👎 tally at that moment, for the vote-then-leader approval. Older rows keep NULLs
            # (approved by the original single ✅). `waiting_notified` makes the "waiting for more 👍 than 👎" reply a once-only.
            for column, ddl in (("approved_by", "TEXT"), ("votes_up", "INTEGER"), ("votes_down", "INTEGER"),
                                ("waiting_notified", "INTEGER NOT NULL DEFAULT 0"), ("last_error", "TEXT")):
                if column not in existing_cols:
                    conn.execute(f"ALTER TABLE pending_approvals ADD COLUMN {column} {ddl}")
            # Each time the copilot raises a request that is a repeat of a card already posted, instead of a second card: what was said
            # and when, so the repeat counts as demand for the original and nothing is lost.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS card_mentions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    approval_id INTEGER NOT NULL, source_key TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL, mentioned_at REAL NOT NULL
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
            # Per-rep Zoom connections (unlike oauth_connections, which is one row per deployment-wide
            # provider), the in-flight authorizations that carry a rep's identity through Zoom's
            # consent screen, and one row per Zoom meeting the bot was sent to (the dedupe key is Zoom's
            # meeting UUID — it is unique per occurrence, unlike the reusable meeting number).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zoom_connections (
                    vexa_user_id TEXT PRIMARY KEY, zoom_user_id TEXT NOT NULL UNIQUE, email TEXT,
                    access_token TEXT NOT NULL, refresh_token TEXT NOT NULL, expires_at REAL NOT NULL,
                    vexa_token TEXT NOT NULL, vexa_token_id TEXT, connected_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zoom_pending (
                    nonce TEXT PRIMARY KEY, vexa_user_id TEXT NOT NULL, vexa_token TEXT NOT NULL,
                    vexa_token_id TEXT, created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS zoom_joins (
                    meeting_uuid TEXT PRIMARY KEY, vexa_user_id TEXT NOT NULL, zoom_meeting_id TEXT, topic TEXT,
                    started_at REAL NOT NULL, outcome TEXT NOT NULL DEFAULT 'pending', detail TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL
                )
            """)

    def is_seen(self, key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM seen_requests WHERE key = ?", (key,)).fetchone()
        return row is not None

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
        """Records the posted card AND marks its `source_key` seen in ONE transaction — if the two
        were separate writes, a failure between them would leave a posted card that is not marked
        seen, and the next SSE replay would post it to Slack a second time."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO pending_approvals "
                "(slack_channel, slack_ts, workspace_id, source_key, title, body, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (slack_channel, slack_ts, workspace_id, source_key, title, body, time.time()),
            )
            conn.execute(
                "INSERT OR IGNORE INTO seen_requests (key, seen_at) VALUES (?, ?)", (source_key, time.time())
            )

    def approve(
        self, *, slack_channel: str, slack_ts: str,
        approved_by: str | None = None, votes_up: int | None = None, votes_down: int | None = None,
    ) -> PendingApproval | None:
        """Idempotent: a second ✅ (or a reaction on an already-approved message) is a no-op, not an error.
        ``approved_by`` and the tally are recorded when the vote-then-leader flow approves it."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE slack_channel = ? AND slack_ts = ? AND status = 'pending'",
                (slack_channel, slack_ts),
            ).fetchone()
            if row is None:
                return None
            now = time.time()
            conn.execute(
                "UPDATE pending_approvals SET status = 'approved', approved_at = ?, approved_by = ?, votes_up = ?, votes_down = ? WHERE id = ?",
                (now, approved_by, votes_up, votes_down, row["id"]),
            )
        return PendingApproval(**{**dict(row), "status": "approved", "approved_at": now,
                                  "approved_by": approved_by, "votes_up": votes_up, "votes_down": votes_down})

    def pending_for_message(self, *, slack_channel: str, slack_ts: str) -> PendingApproval | None:
        """The still-pending feature request this Slack message is the card for, if any."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM pending_approvals WHERE slack_channel = ? AND slack_ts = ? AND status = 'pending'",
                (slack_channel, slack_ts),
            ).fetchone()
        return PendingApproval(**dict(row)) if row else None

    def list_waiting_for_votes(self) -> list[PendingApproval]:
        """Cards a leader already approved that are still waiting for 👍 to outnumber 👎 — re-checked by the sweep, so a vote
        that arrived (or a 👎 that was taken back) without an event reaching us still lets the approval through."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_approvals WHERE status = 'pending' AND waiting_notified = 1").fetchall()
        return [PendingApproval(**dict(r)) for r in rows]

    def known_requests(self, *, workspace_id: str, since: float, source_prefix: str | None = None) -> list[PendingApproval]:
        """The requests a new one could be a repeat of: this workspace's, since ``since``, that have not failed (a request whose
        build failed deserves a fresh card when asked again). ``source_prefix`` narrows to one call's cards."""
        sql = "SELECT * FROM pending_approvals WHERE workspace_id = ? AND created_at >= ? AND status != 'failed'"
        args: list = [workspace_id, since]
        if source_prefix is not None:
            sql += " AND substr(source_key, 1, ?) = ?"
            args += [len(source_prefix), source_prefix]
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY id", args).fetchall()
        return [PendingApproval(**dict(r)) for r in rows]

    def latest_card(self) -> PendingApproval | None:
        """The most recent feature-request card posted to Slack, whatever became of it."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pending_approvals ORDER BY id DESC LIMIT 1").fetchone()
        return PendingApproval(**dict(row)) if row else None

    def get_approval(self, approval_id: int) -> PendingApproval | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone()
        return PendingApproval(**dict(row)) if row else None

    def record_mention(self, *, approval_id: int, source_key: str, title: str, body: str) -> int:
        """Records a repeat of a card and marks its key seen (so a replayed card is not handled twice). Returns how many times the
        request has been raised now, the original included."""
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO card_mentions (approval_id, source_key, title, body, mentioned_at) VALUES (?, ?, ?, ?, ?)",
                (approval_id, source_key, title, body, time.time()),
            )
            conn.execute("INSERT OR IGNORE INTO seen_requests (key, seen_at) VALUES (?, ?)", (source_key, time.time()))
            n = conn.execute("SELECT COUNT(*) FROM card_mentions WHERE approval_id = ?", (approval_id,)).fetchone()[0]
        return 1 + n

    def mention_count(self, approval_id: int) -> int:
        """How many times a request has been raised, the original included."""
        with self._conn() as conn:
            return 1 + conn.execute("SELECT COUNT(*) FROM card_mentions WHERE approval_id = ?", (approval_id,)).fetchone()[0]

    def record_error(self, approval_id: int, message: str) -> bool:
        """Remembers why the latest push / pull-request attempt failed. True when this is a NEW reason for the request (the first failure,
        or a different one), so the caller says it once instead of every sweep."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE pending_approvals SET last_error = ? WHERE id = ? AND (last_error IS NULL OR last_error != ?)",
                               (message, approval_id, message))
            return cur.rowcount == 1

    def clear_error(self, approval_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE pending_approvals SET last_error = NULL WHERE id = ?", (approval_id,))

    def claim_waiting_notice(self, approval_id: int) -> bool:
        """True exactly once per request: the caller that gets it posts the "waiting for more 👍 than 👎" reply."""
        with self._conn() as conn:
            cur = conn.execute("UPDATE pending_approvals SET waiting_notified = 1 WHERE id = ? AND waiting_notified = 0 AND status = 'pending'", (approval_id,))
            return cur.rowcount == 1

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
        a LATER sweep can no longer prove the push happened (the worktree is released once the PR opens,
        or reaped by age), so PR-open retries are driven by this store state, not by re-checking git."""
        with self._conn() as conn:
            conn.execute("UPDATE pending_approvals SET status = 'pushed', pushed_at = ? WHERE id = ?",
                         (time.time(), approval_id))

    def list_pushed_unopened(self) -> list[PendingApproval]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM pending_approvals WHERE status = 'pushed'").fetchall()
        return [PendingApproval(**dict(r)) for r in rows]

    def mark_done(self, approval_id: int, *, pr_url: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE pending_approvals SET status = 'done', done_at = ?, pr_url = ? WHERE id = ?",
                         (time.time(), pr_url, approval_id))

    # ── Zoom (per-rep) ────────────────────────────────────────────────────────────────────────────

    def add_zoom_pending(self, *, nonce: str, vexa_user_id: str, vexa_token: str, vexa_token_id: str | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO zoom_pending (nonce, vexa_user_id, vexa_token, vexa_token_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (nonce, vexa_user_id, vexa_token, vexa_token_id, time.time()),
            )

    def take_zoom_pending(self, nonce: str, *, max_age_sec: float) -> ZoomPending | None:
        """Single-use: the row is deleted whether or not it is still fresh, so a replayed callback finds nothing."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM zoom_pending WHERE nonce = ?", (nonce,)).fetchone()
            conn.execute("DELETE FROM zoom_pending WHERE nonce = ?", (nonce,))
            conn.execute("DELETE FROM zoom_pending WHERE created_at < ?", (time.time() - max_age_sec,))
        if row is None or time.time() - row["created_at"] > max_age_sec:
            return None
        return ZoomPending(**dict(row))

    def save_zoom_connection(
        self, *, vexa_user_id: str, zoom_user_id: str, email: str | None, access_token: str, refresh_token: str,
        expires_at: float, vexa_token: str, vexa_token_id: str | None,
    ) -> None:
        """One Zoom account belongs to one Vexa user: connecting it from a second Vexa user moves it there."""
        with self._conn() as conn:
            conn.execute("DELETE FROM zoom_connections WHERE zoom_user_id = ? AND vexa_user_id != ?",
                         (zoom_user_id, vexa_user_id))
            conn.execute(
                "INSERT OR REPLACE INTO zoom_connections (vexa_user_id, zoom_user_id, email, access_token, "
                "refresh_token, expires_at, vexa_token, vexa_token_id, connected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (vexa_user_id, zoom_user_id, email, access_token, refresh_token, expires_at, vexa_token,
                 vexa_token_id, time.time()),
            )

    def update_zoom_tokens(self, vexa_user_id: str, *, access_token: str, refresh_token: str, expires_at: float) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE zoom_connections SET access_token = ?, refresh_token = ?, expires_at = ? WHERE vexa_user_id = ?",
                (access_token, refresh_token, expires_at, vexa_user_id),
            )

    def get_zoom_connection(self, vexa_user_id: str) -> ZoomConnection | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM zoom_connections WHERE vexa_user_id = ?", (vexa_user_id,)).fetchone()
        return ZoomConnection(**dict(row)) if row else None

    def get_zoom_connection_by_zoom_user(self, zoom_user_id: str) -> ZoomConnection | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM zoom_connections WHERE zoom_user_id = ?", (zoom_user_id,)).fetchone()
        return ZoomConnection(**dict(row)) if row else None

    def delete_zoom_connection(self, vexa_user_id: str) -> ZoomConnection | None:
        """Returns what was removed, so the caller can revoke the Zoom token and the Vexa key it held."""
        existing = self.get_zoom_connection(vexa_user_id)
        with self._conn() as conn:
            conn.execute("DELETE FROM zoom_connections WHERE vexa_user_id = ?", (vexa_user_id,))
        return existing

    def claim_zoom_join(self, *, meeting_uuid: str, vexa_user_id: str, zoom_meeting_id: str, topic: str | None) -> bool:
        """True the first time this meeting occurrence is seen — Zoom redelivers a notification it did not get a
        timely 200 for, and the bot must go to a meeting once."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO zoom_joins (meeting_uuid, vexa_user_id, zoom_meeting_id, topic, started_at) "
                "VALUES (?, ?, ?, ?, ?)", (meeting_uuid, vexa_user_id, zoom_meeting_id, topic, time.time()),
            )
            return cur.rowcount == 1

    def record_zoom_join_outcome(self, meeting_uuid: str, *, outcome: str, detail: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE zoom_joins SET outcome = ?, detail = ? WHERE meeting_uuid = ?",
                         (outcome, detail, meeting_uuid))

    def last_zoom_join(self, vexa_user_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT topic, zoom_meeting_id, started_at, outcome, detail FROM zoom_joins WHERE vexa_user_id = ? "
                "ORDER BY started_at DESC LIMIT 1", (vexa_user_id,),
            ).fetchone()
        return dict(row) if row else None

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

    def get_runtime_setting(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM runtime_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row is not None else None

    def set_runtime_setting(self, key: str, value: str) -> None:
        """Empty `value` CLEARS the override (deletes the row) instead of storing an empty string —
        same empty-field-clears-it convention the Settings page's ConfigForm already uses everywhere
        else, so a Save with a blanked field reverts to the env-var default rather than locking in
        an empty override that would read as "configured" but post nowhere."""
        with self._conn() as conn:
            if value:
                conn.execute(
                    "INSERT INTO runtime_settings (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                    (key, value, time.time()),
                )
            else:
                conn.execute("DELETE FROM runtime_settings WHERE key = ?", (key,))
