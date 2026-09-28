# syntax=docker/dockerfile:1
# sales-cycle — the HubSpot/Slack/GitHub add-on. Talks to Vexa only over its existing HTTP API (plus
# reading the shared agent-workspaces volume directly for feature-request notes — see
# entity_files.py); never imports Vexa's own core packages.
FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.9.22

ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY integrations/sales-cycle/pyproject.toml integrations/sales-cycle/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-default-groups

COPY integrations/sales-cycle/src ./src

EXPOSE 8200
CMD ["uvicorn", "sales_cycle.api:app", "--host", "0.0.0.0", "--port", "8200"]
