from sales_cycle.store import Store


def _store() -> Store:
    return Store(":memory:")


def test_seen_files_roundtrip():
    s = _store()
    assert s.is_seen("a.md") is False
    s.mark_seen("a.md")
    assert s.is_seen("a.md") is True
    assert s.is_seen("b.md") is False


def test_mark_seen_is_idempotent():
    s = _store()
    s.mark_seen("a.md")
    s.mark_seen("a.md")  # must not raise (UNIQUE/PRIMARY KEY conflict handled)
    assert s.is_seen("a.md") is True


def test_approve_flow():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="123.456", workspace_id="cust-1",
        entity_path="/workspaces/cust-1/kg/entities/feature_request/csv-export.md",
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
        entity_path="x.md", title="X", body="y",
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
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", entity_path="x.md", title="X", body="y",
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
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", entity_path="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    assert s.claim_for_dispatch(approved.id) is True
    s.revert_to_approved(approved.id)
    assert [p.id for p in s.list_approved_unprocessed()] == [approved.id]
    assert s.claim_for_dispatch(approved.id) is True  # claimable again after the revert


def test_mark_done_removes_from_unprocessed_queue():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", entity_path="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.mark_done(approved.id)
    assert s.list_approved_unprocessed() == []


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
