"""gate:health — sales-cycle exposes a conforming liveness /health.

The gate discovers this file by name (scripts/gates.mjs gateHealth) for every Python package that
builds a FastAPI app, and a standing service without one is a RED rather than a green-on-empty: a
process nobody can probe is a process nobody can restart on evidence.
"""
from conftest import client


def test_health_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "sales-cycle"}


def test_health_takes_no_credential():
    """Every other route on this surface takes X-API-Key or an OAuth session. If this one ever
    acquires one, an orchestrator probing liveness without a key reads 401 — indistinguishable from
    a dead process to a restart policy."""
    resp = client.get("/health", headers={})
    assert resp.status_code == 200
