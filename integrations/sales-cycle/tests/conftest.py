"""Shared test setup — one `TestClient` and one `GATEWAY` constant, instead of every API test file
building its own copy (and, in one file, hardcoding the gateway URL instead of reading it from
settings the way the others do, which could silently drift out of sync)."""

import pytest
from fastapi.testclient import TestClient

import sales_cycle.api as api_module
from sales_cycle.api import app
from sales_cycle.settings import get_settings

# The service's private routes need the shared secret (internal_auth.py); `client` carries it the way the Terminal and the sweep loop do,
# and `bare` is a caller without it — what the internet looks like.
INTERNAL_SECRET = "internal-secret"
client = TestClient(app, headers={"X-Internal-Secret": INTERNAL_SECRET})
bare = TestClient(app)
GATEWAY = get_settings().vexa_gateway_url.rstrip("/")
AGENT_API = get_settings().agent_api_internal_url.rstrip("/")
MEETING_API = get_settings().meeting_api_internal_url.rstrip("/")


@pytest.fixture(autouse=True)
def _internal_secret(monkeypatch):
    monkeypatch.setenv("SALES_CYCLE_INTERNAL_SECRET", INTERNAL_SECRET)


@pytest.fixture(autouse=True)
def _fresh_sales_cycle_store(tmp_path, monkeypatch):
    """Every test gets its own throwaway database, automatically — the real default
    (SALES_CYCLE_DB_PATH's default of /data/sales-cycle.db) isn't writable outside a real deployment,
    and even where it would be, tests must never share state with each other or a real install."""
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))
    # _watch_meeting_tasks is a plain module-level dict of real asyncio.Tasks, meant to live for the
    # WHOLE process in a real deployment — nothing to reset there. But a test session doesn't restart
    # the process between tests, and a task scheduled by one test may not have run its done-callback
    # yet by the time the next test starts (create_task only SCHEDULES, it doesn't run synchronously)
    # — without this, a stale entry from an earlier test could make a later test's "is this meeting
    # already being watched?" check silently wrong.
    api_module._watch_meeting_tasks.clear()
    yield
    api_module._store = None
    api_module._watch_meeting_tasks.clear()
