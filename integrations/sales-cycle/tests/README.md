# tests

Unit + L2 tests for the `sales_cycle` package, one file per module under `src/sales_cycle/` (e.g.
`test_store.py` for `store.py`). `conftest.py` holds the shared `TestClient` + `GATEWAY` fixture
every API test file uses, plus the autouse fixture that gives each test its own throwaway sqlite
database.
