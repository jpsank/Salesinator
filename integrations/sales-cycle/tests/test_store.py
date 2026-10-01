import sqlite3

from sales_cycle.store import Store


def _store() -> Store:
    return Store(":memory:")


def test_entity_path_migrates_to_source_key_on_a_real_pre_existing_db(tmp_path):
    """Reproduced live: a real deployment's database, created before the entity_path -> source_key
    rename, crashed on the very first real record_pending_approval call — successfully posted to
    Slack, then failed to persist that it had, so nothing ever marked the card as seen. Build the
    OLD schema by hand (Store's own CREATE TABLE IF NOT EXISTS would never touch a pre-existing
    table), then confirm opening it through Store migrates it and a real insert works afterward."""
    db_path = str(tmp_path / "old-schema.db")
    old = sqlite3.connect(db_path)
    old.execute("""
        CREATE TABLE pending_approvals (
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
    old.commit()
    old.close()

    s = Store(db_path)  # migration runs in _init()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1",
        source_key="x.md", title="CSV export", body="wants it",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    assert approved is not None
    assert approved.title == "CSV export"


def test_seen_requests_roundtrip():
    s = _store()
    assert s.is_seen("live:1:csv export") is False
    s.mark_seen("live:1:csv export")
    assert s.is_seen("live:1:csv export") is True
    assert s.is_seen("live:1:sso") is False


def test_mark_seen_is_idempotent():
    s = _store()
    s.mark_seen("live:1:csv export")
    s.mark_seen("live:1:csv export")  # must not raise (UNIQUE/PRIMARY KEY conflict handled)
    assert s.is_seen("live:1:csv export") is True


def test_approve_flow():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="123.456", workspace_id="cust-1",
        source_key="live:1:csv export",
        title="CSV export", body="wants it",
    )
    approved = s.approve(slack_channel="C1", slack_ts="123.456")
    assert approved is not None
    assert approved.title == "CSV export"
    assert approved.status == "approved"

    pending = s.list_approved_unprocessed()
    assert len(pending) == 1
    assert pending[0].workspace_id == "cust-1"


def test_approve_is_idempotent_second_reaction_is_noop():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="123.456", workspace_id="cust-1",
        source_key="x.md", title="X", body="y",
    )
    first = s.approve(slack_channel="C1", slack_ts="123.456")
    second = s.approve(slack_channel="C1", slack_ts="123.456")
    assert first is not None
    assert second is None  # already approved — not re-approved, not an error


def test_approve_unknown_message_returns_none():
    s = _store()
    assert s.approve(slack_channel="C1", slack_ts="does-not-exist") is None


def test_claim_for_dispatch_is_race_safe():
    """Two callers (the real-time Slack path and the cron sweep) racing on the same approval must
    never both win the claim — a second implementation turn on the same shared workspace is a
    correctness bug, not just wasted work."""
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    assert s.claim_for_dispatch(approved.id) is True
    assert s.claim_for_dispatch(approved.id) is False  # already claimed — the loser
    assert s.list_approved_unprocessed() == []  # 'dispatching', not 'approved' — the cron sweep skips it too


def test_claim_for_dispatch_unknown_id_returns_false():
    s = _store()
    assert s.claim_for_dispatch(999) is False


def test_revert_to_approved_undoes_a_failed_claim():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    assert s.claim_for_dispatch(approved.id) is True
    s.revert_to_approved(approved.id)
    assert [p.id for p in s.list_approved_unprocessed()] == [approved.id]
    assert s.claim_for_dispatch(approved.id) is True  # claimable again after the revert


def test_mark_done_removes_from_unprocessed_queue():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.mark_done(approved.id)
    assert s.list_approved_unprocessed() == []


def test_mark_pushed_moves_from_dispatched_unpushed_to_pushed_unopened():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.claim_for_dispatch(approved.id)
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-1")
    assert [a.id for a in s.list_dispatched_unpushed()] == [approved.id]
    assert s.list_pushed_unopened() == []

    s.mark_pushed(approved.id)

    assert s.list_dispatched_unpushed() == []
    pushed = s.list_pushed_unopened()
    assert len(pushed) == 1
    assert pushed[0].id == approved.id
    assert pushed[0].workload_id == "unit-1"  # still carries the unit_id — the PR-open step needs it


def test_mark_done_from_pushed_removes_from_pushed_unopened_queue():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.claim_for_dispatch(approved.id)
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-1")
    s.mark_pushed(approved.id)

    s.mark_done(approved.id)

    assert s.list_pushed_unopened() == []


def test_mark_dispatched_sets_dispatched_at_and_increments_attempts():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.claim_for_dispatch(approved.id)
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-1")
    row = s.list_dispatched_unpushed()[0]
    assert row.dispatched_at is not None
    assert row.dispatch_attempts == 1


def test_fail_or_retry_stale_dispatch_retries_under_the_cap():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.claim_for_dispatch(approved.id)
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-1")  # attempt 1 of 2

    assert s.fail_or_retry_stale_dispatch(approved.id, max_attempts=2) == "retried"
    assert s.list_dispatched_unpushed() == []
    assert [a.id for a in s.list_approved_unprocessed()] == [approved.id]
    # dispatch_attempts is NOT reset by the retry itself — only a fresh mark_dispatched bumps it,
    # so a second timeout on the SAME attempt count correctly hits the cap next time.
    assert s.claim_for_dispatch(approved.id) is True
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-2")
    assert s.list_dispatched_unpushed()[0].dispatch_attempts == 2


def test_fail_or_retry_stale_dispatch_fails_at_the_cap():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.claim_for_dispatch(approved.id)
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-1")  # attempt 1 of 1 (cap)

    assert s.fail_or_retry_stale_dispatch(approved.id, max_attempts=1) == "failed"
    assert s.list_dispatched_unpushed() == []
    assert s.list_approved_unprocessed() == []  # not left retryable — a human has to look at it


def test_fail_or_retry_stale_dispatch_skips_a_row_that_already_moved_on():
    """A race with the normal push-check sweep: the row reached 'pushed' before the staleness check
    got to it. Must not clobber real progress back to 'approved' or 'failed'."""
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", source_key="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.claim_for_dispatch(approved.id)
    s.mark_dispatched(approved.id, branch="feature/x", workload_id="unit-1")
    s.mark_pushed(approved.id)

    assert s.fail_or_retry_stale_dispatch(approved.id, max_attempts=1) == "skipped"
    assert [a.id for a in s.list_pushed_unopened()] == [approved.id]  # untouched


def test_oauth_connection_roundtrip():
    s = _store()
    assert s.get_oauth_connection("hubspot") is None
    s.save_oauth_connection(
        provider="hubspot", access_token="at-1", refresh_token="rt-1",
        expires_at=1000.0, account_label="acme.hubspot.com",
    )
    conn = s.get_oauth_connection("hubspot")
    assert conn is not None
    assert conn.access_token == "at-1"
    assert conn.account_label == "acme.hubspot.com"


def test_oauth_connection_reconnect_replaces_but_keeps_original_connected_at():
    s = _store()
    s.save_oauth_connection(
        provider="hubspot", access_token="at-1", refresh_token="rt-1",
        expires_at=1000.0, account_label="acme.hubspot.com",
    )
    first = s.get_oauth_connection("hubspot")
    s.save_oauth_connection(
        provider="hubspot", access_token="at-2", refresh_token="rt-2",
        expires_at=2000.0, account_label="acme.hubspot.com",
    )
    second = s.get_oauth_connection("hubspot")
    assert second.access_token == "at-2"
    assert second.connected_at == first.connected_at  # a refresh/reconnect isn't a NEW connection


def test_disconnect_oauth_removes_connection():
    s = _store()
    s.save_oauth_connection(
        provider="hubspot", access_token="at-1", refresh_token="rt-1",
        expires_at=1000.0, account_label=None,
    )
    s.disconnect_oauth("hubspot")
    assert s.get_oauth_connection("hubspot") is None


def test_active_watchers_roundtrip():
    s = _store()
    assert s.list_active_watchers() == []
    s.record_watcher_started("61", "7")
    assert s.list_active_watchers() == [("61", "7")]


def test_record_watcher_started_is_idempotent_not_duplicated():
    """A restart re-registering the SAME meeting_id refreshes it, never adds a second row —
    meeting_id is a real primary key, not just a convention."""
    s = _store()
    s.record_watcher_started("61", "7")
    s.record_watcher_started("61", "7")
    assert s.list_active_watchers() == [("61", "7")]


def test_record_watcher_stopped_removes_it():
    s = _store()
    s.record_watcher_started("61", "7")
    s.record_watcher_stopped("61")
    assert s.list_active_watchers() == []


def test_record_watcher_stopped_on_an_unknown_meeting_is_a_noop():
    s = _store()
    s.record_watcher_stopped("does-not-exist")  # must not raise
    assert s.list_active_watchers() == []


def test_runtime_setting_roundtrip():
    s = _store()
    assert s.get_runtime_setting("slack_channel_id") is None
    s.set_runtime_setting("slack_channel_id", "C123")
    assert s.get_runtime_setting("slack_channel_id") == "C123"


def test_runtime_setting_overwrite_replaces_value():
    s = _store()
    s.set_runtime_setting("slack_channel_id", "C123")
    s.set_runtime_setting("slack_channel_id", "C456")
    assert s.get_runtime_setting("slack_channel_id") == "C456"


def test_runtime_setting_empty_value_clears_it():
    """set_runtime_setting's own contract: an empty string DELETES the row rather than storing an
    empty value — so a cleared override reads back as None (falls through to the caller's env
    default) instead of a configured-but-blank override that would look set but post nowhere."""
    s = _store()
    s.set_runtime_setting("slack_channel_id", "C123")
    s.set_runtime_setting("slack_channel_id", "")
    assert s.get_runtime_setting("slack_channel_id") is None


def test_runtime_setting_keys_are_independent():
    s = _store()
    s.set_runtime_setting("slack_channel_id", "C123")
    assert s.get_runtime_setting("some_other_key") is None
