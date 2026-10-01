"""Runtime.reap_stopped — the destroy-sweep for stopped workloads. stop() deliberately never
destroys (see kernel.py's docstring): a consumer (meeting-api's reconcile) polls shortly after a
clean exit expecting positive evidence (state=stopped), not a 404. reap_stopped is the delayed,
bounded cleanup that eventually reclaims those records once that window has safely passed."""
from datetime import datetime, timedelta, timezone

from runtime_kernel import ProcessBackend, Runtime, RuntimeState, WorkloadSpec


def _runtime():
    return Runtime(backend=ProcessBackend(), profiles={"quick": ["true"]}, grace_sec=2.0)


def _backdate_stop(rt, workload_id, seconds_ago):
    record = rt.store.get(workload_id)
    record.status.stoppedAt = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()
    rt.store.set(record)


def test_reap_stopped_destroys_only_past_retention():
    rt = _runtime()
    rt.create(WorkloadSpec(workloadId="old", profile="quick", env={}))
    rt.stop("old")
    _backdate_stop(rt, "old", seconds_ago=7200)

    rt.create(WorkloadSpec(workloadId="recent", profile="quick", env={}))
    rt.stop("recent")
    _backdate_stop(rt, "recent", seconds_ago=60)

    reaped = rt.reap_stopped(older_than_sec=3600)

    assert reaped == ["old"]
    assert rt.get("old").state is RuntimeState.destroyed
    assert rt.get("recent").state is RuntimeState.stopped


def test_reap_stopped_ignores_running_and_destroyed():
    rt = _runtime()
    rt.create(WorkloadSpec(workloadId="running", profile="quick", env={}))

    rt.create(WorkloadSpec(workloadId="gone", profile="quick", env={}))
    rt.stop("gone")
    _backdate_stop(rt, "gone", seconds_ago=7200)
    rt.destroy("gone")

    assert rt.reap_stopped(older_than_sec=3600) == []

    rt.stop("running")  # cleanup the real child


def test_reap_stopped_is_a_noop_with_nothing_past_retention():
    rt = _runtime()
    rt.create(WorkloadSpec(workloadId="w1", profile="quick", env={}))
    rt.stop("w1")

    assert rt.reap_stopped(older_than_sec=3600) == []
    assert rt.get("w1").state is RuntimeState.stopped
