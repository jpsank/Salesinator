"""pipeline_stats — the numbers behind "built before the call ends": stage counts, per-hop timings, retries."""
import sqlite3

from sales_cycle.report import pipeline_stats
from sales_cycle.store import Store


def _request(s: Store, ts: str) -> int:
    s.record_pending_approval(slack_channel="C1", slack_ts=ts, workspace_id="w", source_key=f"k{ts}", title=f"t{ts}", body="b")
    return s.approve(slack_channel="C1", slack_ts=ts).id


def _set(s: Store, id_: int, **cols) -> None:
    with s._conn() as conn:
        for col, val in cols.items():
            conn.execute(f"UPDATE pending_approvals SET {col} = ? WHERE id = ?", (val, id_))


def test_stage_times_and_the_pr_link_are_recorded():
    s = Store(":memory:")
    approval_id = _request(s, "1.0")
    s.mark_dispatched(approval_id, branch="feature/x-1", workload_id="u1")
    s.mark_pushed(approval_id)
    s.mark_done(approval_id, pr_url="https://github.com/o/r/pull/7")
    with s._conn() as conn:
        row = dict(conn.execute("SELECT * FROM pending_approvals WHERE id = ?", (approval_id,)).fetchone())
    assert row["approved_at"] and row["pushed_at"] and row["done_at"]
    assert row["pr_url"] == "https://github.com/o/r/pull/7"
    assert row["created_at"] <= row["approved_at"] <= row["pushed_at"] <= row["done_at"]


def test_pipeline_stats_counts_stages_and_times_each_hop():
    s = Store(":memory:")
    done = _request(s, "1.0")
    _set(s, done, status="done", created_at=100.0, approved_at=130.0, pushed_at=430.0, done_at=445.0,
         pr_url="https://github.com/o/r/pull/1", dispatch_attempts=1)
    failed = _request(s, "2.0")
    _set(s, failed, status="failed", created_at=200.0, approved_at=210.0, dispatch_attempts=3)
    s.record_pending_approval(slack_channel="C1", slack_ts="3.0", workspace_id="w", source_key="k3", title="t3", body="b")

    stats = pipeline_stats(s)
    assert stats["requests"] == 3
    assert stats["by_status"]["done"] == 1 and stats["by_status"]["failed"] == 1 and stats["by_status"]["pending"] == 1
    assert stats["approval_rate"] == round(2 / 3, 3)
    assert stats["reached_pr_rate"] == 0.5 and stats["failed_rate"] == 0.5
    assert stats["retried"] == 1
    assert stats["hops"]["approved_to_pushed"] == {"n": 1, "median_s": 300.0, "p90_s": 300.0, "max_s": 300.0}
    assert stats["hops"]["posted_to_pr"]["median_s"] == 345.0
    assert stats["hops"]["posted_to_approved"]["n"] == 2        # the failed one was approved too
    assert stats["pull_requests"] == ["https://github.com/o/r/pull/1"]


def test_rows_from_before_the_stage_columns_are_counted_but_never_skew_a_timing(tmp_path):
    db = str(tmp_path / "old.db")
    old = sqlite3.connect(db)
    old.execute("""CREATE TABLE pending_approvals (id INTEGER PRIMARY KEY AUTOINCREMENT, slack_channel TEXT NOT NULL,
        slack_ts TEXT NOT NULL, workspace_id TEXT NOT NULL, source_key TEXT NOT NULL, title TEXT NOT NULL,
        body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', branch TEXT, workload_id TEXT,
        created_at REAL NOT NULL, dispatched_at REAL, dispatch_attempts INTEGER NOT NULL DEFAULT 0,
        UNIQUE(slack_channel, slack_ts))""")
    old.execute("INSERT INTO pending_approvals (slack_channel, slack_ts, workspace_id, source_key, title, body, status, created_at)"
                " VALUES ('C','1','w','k','t','b','done',50.0)")
    old.commit(); old.close()

    stats = pipeline_stats(Store(db))
    assert stats["requests"] == 1 and stats["by_status"]["done"] == 1
    assert all(h["n"] == 0 for h in stats["hops"].values())


def test_since_limits_the_window():
    s = Store(":memory:")
    old = _request(s, "1.0"); _set(s, old, created_at=10.0)
    new = _request(s, "2.0"); _set(s, new, created_at=1000.0)
    assert pipeline_stats(s, since=500.0)["requests"] == 1
