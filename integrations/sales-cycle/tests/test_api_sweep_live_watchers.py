"""POST /internal/sweep-live-watchers — the self-heal for a real, reproduced-live gap:
watch_meeting's own docstring already named it ("nothing here notices or restarts a watcher lost to
a sales-cycle restart mid-call"). active_watchers (the store) is the durable record that survives a
restart; _watch_meeting_tasks (in-process) does not — this sweep reconciles the two against
meeting-api's own live status.
"""
import httpx
import respx

import sales_cycle.api as api_module
from conftest import MEETING_API, client


class _NotDone:
    """A stand-in for a real, still-running asyncio.Task — the sweep only ever calls `.done()` on
    whatever's in _watch_meeting_tasks, so this is enough without a real event loop in a sync test."""
    def done(self) -> bool:
        return False


def _stub_watch_meeting(monkeypatch) -> list:
    """Real enough to exercise _start_watcher's asyncio.create_task path (this file's whole point is
    the sweep's RESTART decision, not watch_meeting's own behavior — covered by
    test_live_card_watcher.py)."""
    calls = []

    async def _fake(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(api_module, "watch_meeting", _fake)
    return calls


def test_sweep_with_no_active_watchers_does_nothing():
    resp = client.post("/internal/sweep-live-watchers")
    assert resp.status_code == 200
    assert resp.json() == {"restarted": [], "stopped": []}


def test_sweep_skips_a_meeting_whose_watcher_is_already_running():
    api_module.get_store().record_watcher_started("61", "7")
    api_module._watch_meeting_tasks["61"] = _NotDone()

    resp = client.post("/internal/sweep-live-watchers")

    assert resp.status_code == 200
    assert resp.json() == {"restarted": [], "stopped": []}


@respx.mock
def test_sweep_restarts_a_missing_watcher_for_a_still_live_meeting(monkeypatch):
    """The exact scenario reproduced live: a real feature_request card was correctly tagged, but the
    watcher had died with a previous process — the meeting is STILL genuinely live."""
    calls = _stub_watch_meeting(monkeypatch)
    api_module.get_store().record_watcher_started("61", "7")
    respx.get(f"{MEETING_API}/meetings/61").mock(
        return_value=httpx.Response(200, json={"id": 61, "status": "active"})
    )

    resp = client.post("/internal/sweep-live-watchers")

    assert resp.status_code == 200
    assert resp.json() == {"restarted": ["61"], "stopped": []}
    assert len(calls) == 1
    assert calls[0]["meeting_id"] == "61"
    assert calls[0]["subject"] == "7"
    # the durable record is still there — this meeting still needs watching
    assert api_module.get_store().list_active_watchers() == [("61", "7")]


@respx.mock
def test_sweep_cleans_up_a_meeting_that_reached_a_terminal_status(monkeypatch):
    calls = _stub_watch_meeting(monkeypatch)
    api_module.get_store().record_watcher_started("61", "7")
    respx.get(f"{MEETING_API}/meetings/61").mock(
        return_value=httpx.Response(200, json={"id": 61, "status": "completed"})
    )

    resp = client.post("/internal/sweep-live-watchers")

    assert resp.status_code == 200
    assert resp.json() == {"restarted": [], "stopped": ["61"]}
    assert calls == []
    assert api_module.get_store().list_active_watchers() == []


@respx.mock
def test_sweep_cleans_up_a_meeting_meeting_api_no_longer_has(monkeypatch):
    calls = _stub_watch_meeting(monkeypatch)
    api_module.get_store().record_watcher_started("61", "7")
    respx.get(f"{MEETING_API}/meetings/61").mock(return_value=httpx.Response(404))

    resp = client.post("/internal/sweep-live-watchers")

    assert resp.status_code == 200
    assert resp.json() == {"restarted": [], "stopped": ["61"]}
    assert calls == []


@respx.mock
def test_sweep_assumes_still_live_on_a_transport_failure_rather_than_abandoning_it(monkeypatch):
    """A transient meeting-api blip must never look like "the meeting ended" — the cost of an
    unnecessary restart attempt is far lower than silently abandoning a real, still-live meeting."""
    calls = _stub_watch_meeting(monkeypatch)
    api_module.get_store().record_watcher_started("61", "7")
    respx.get(f"{MEETING_API}/meetings/61").mock(side_effect=httpx.ConnectError("refused"))

    resp = client.post("/internal/sweep-live-watchers")

    assert resp.status_code == 200
    assert resp.json() == {"restarted": ["61"], "stopped": []}
    assert len(calls) == 1
