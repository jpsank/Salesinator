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


def test_mark_done_removes_from_unprocessed_queue():
    s = _store()
    s.record_pending_approval(
        slack_channel="C1", slack_ts="1", workspace_id="cust-1", entity_path="x.md", title="X", body="y",
    )
    approved = s.approve(slack_channel="C1", slack_ts="1")
    s.mark_done(approved.id)
    assert s.list_approved_unprocessed() == []
