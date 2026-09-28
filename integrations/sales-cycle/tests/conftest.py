"""Shared test setup — one `TestClient` and one `GATEWAY` constant, instead of every API test file
building its own copy (and, in one file, hardcoding the gateway URL instead of reading it from
settings the way the others do, which could silently drift out of sync)."""

import pytest
from fastapi.testclient import TestClient

import sales_cycle.api as api_module
from sales_cycle.api import app
from sales_cycle.settings import get_settings

client = TestClient(app)
GATEWAY = get_settings().vexa_gateway_url.rstrip("/")


@pytest.fixture(autouse=True)
def _fresh_sales_cycle_store(tmp_path, monkeypatch):
    """Every test gets its own throwaway database, automatically — the real default
    (SALES_CYCLE_DB_PATH's default of /data/sales-cycle.db) isn't writable outside a real deployment,
    and even where it would be, tests must never share state with each other or a real install."""
    api_module._store = None
    monkeypatch.setenv("SALES_CYCLE_DB_PATH", str(tmp_path / "sales-cycle.db"))
    yield
    api_module._store = None
