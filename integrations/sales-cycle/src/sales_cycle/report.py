"""report.py — how the request→PR pipeline is actually doing, read from the store.

``pipeline_stats`` answers the questions a claim like "built before the call ends" rests on: how many requests
reached each stage, how long each hop took, how often a build was retried or gave up. Timings use only the
requests that recorded both ends of a hop, so older rows without stage timestamps never skew them.

Run it against a live database::

    docker exec vexa-v012-sales-cycle-1 python -m sales_cycle.report
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

from sales_cycle.store import Store

_STATUSES = ("pending", "approved", "dispatching", "dispatched", "pushed", "done", "failed")

# (label, earlier column, later column) — each hop of the pipeline.
_HOPS = (
    ("posted_to_approved", "created_at", "approved_at"),
    ("approved_to_pushed", "approved_at", "pushed_at"),
    ("pushed_to_pr", "pushed_at", "done_at"),
    ("posted_to_pr", "created_at", "done_at"),
)


def _summary(seconds: list[float]) -> dict:
    if not seconds:
        return {"n": 0}
    ordered = sorted(seconds)
    return {
        "n": len(ordered),
        "median_s": round(statistics.median(ordered), 1),
        "p90_s": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1),
        "max_s": round(ordered[-1], 1),
    }


def pipeline_stats(store: Store, *, since: float | None = None) -> dict:
    with store._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM pending_approvals").fetchall()]
    if since is not None:
        rows = [r for r in rows if r["created_at"] >= since]
    by_status = {s: 0 for s in _STATUSES}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    hops = {
        label: _summary([r[b] - r[a] for r in rows if r.get(a) is not None and r.get(b) is not None])
        for label, a, b in _HOPS
    }
    decided = [r for r in rows if r["status"] != "pending"]
    return {
        "requests": len(rows),
        "by_status": by_status,
        "approval_rate": round(len(decided) / len(rows), 3) if rows else None,
        "reached_pr_rate": round(by_status["done"] / len(decided), 3) if decided else None,
        "failed_rate": round(by_status["failed"] / len(decided), 3) if decided else None,
        "retried": sum(1 for r in rows if (r.get("dispatch_attempts") or 0) > 1),
        "hops": hops,
        "pull_requests": [r["pr_url"] for r in rows if r.get("pr_url")],
    }


def main(argv: list[str] | None = None) -> None:
    from sales_cycle.settings import get_settings

    parser = argparse.ArgumentParser(description="Request→PR pipeline statistics from the sales-cycle store.")
    parser.add_argument("--db", default=None, help="database path (default: the configured SALES_CYCLE_DB_PATH)")
    parser.add_argument("--days", type=float, default=None, help="only requests created in the last N days")
    args = parser.parse_args(argv)
    store = Store(args.db or get_settings().db_path)
    since = time.time() - args.days * 86400 if args.days else None
    print(json.dumps(pipeline_stats(store, since=since), indent=2))


if __name__ == "__main__":
    main()
