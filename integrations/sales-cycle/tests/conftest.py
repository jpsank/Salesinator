"""Shared test setup — one `TestClient` and one `GATEWAY` constant, instead of every API test file
building its own copy (and, in one file, hardcoding the gateway URL instead of reading it from
settings the way the others do, which could silently drift out of sync)."""

from fastapi.testclient import TestClient

from sales_cycle.api import app
from sales_cycle.settings import get_settings

client = TestClient(app)
GATEWAY = get_settings().vexa_gateway_url.rstrip("/")
