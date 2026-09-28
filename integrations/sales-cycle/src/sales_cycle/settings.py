"""Config for the sales-cycle integration — a validated contract delivered by env (P14 in spirit;
this package lives outside core/ so it isn't gated by config.v1, but follows the same discipline)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALES_CYCLE_")

    # Vexa's own gateway — the only way this package talks to Vexa (external HTTP client, no in-process
    # imports of core.meetings internals).
    vexa_gateway_url: str = "http://gateway:8000"

    # HubSpot private-app token (plain HTTP client, no SDK — Category-A licensing, ADR-0004).
    hubspot_token: str = ""
    hubspot_base_url: str = "https://api.hubapi.com"

    # The workspace slug a meeting falls back to when no customer_tag was given and no calendar match
    # resolved one. Never blocks the meeting — just means a human triages it later.
    unmapped_workspace_slug: str = "unmapped"

    # Slack — Bot API (not an incoming webhook: we need the message ts back to correlate a ✅ reaction).
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # Where the agent-workspaces volume is mounted read-only into this service (matches core/agent's own
    # `workspaces_dir` default, core/agent/shared/config.py:56).
    workspaces_root: str = "/workspaces"

    db_path: str = "/data/sales-cycle.db"

    # Phase 4 — the orchestrator dispatches implementation turns and reads/pushes the product-repo
    # workspace's git state DIRECTLY against agent-api's internal port, not through the gateway.
    #
    # Why: agent-api's own docs say public clients reach it at `$API_BASE/agent/*` through the
    # gateway — but on THIS deployment `POST /agent/invocations` (and its siblings) 404 through the
    # gateway today (verified live during this build: `GET /agent/health` and `POST /agent/invocations`
    # both 404 via the gateway, while the SAME routes work when reached directly on agent-api's
    # internal port). agent-api is not published on a host port in deploy/compose (only reachable
    # inside the compose network), so a sibling container calling it directly is a legitimate
    # internal-network call, not a public bypass — this assumption holds for the dev/self-host
    # topology this was built against. A hardened deployment that sets VEXA_REQUIRE_GATEWAY_IDENTITY
    # will refuse this (agent-api's own `subject_of()` demands a gateway-signed `X-Gateway-Verified`
    # header in that mode) — revisit then, either by fixing the gateway's route table or minting
    # this service a proper gateway-verified path.
    agent_api_internal_url: str = "http://agent-api:8100"

    # The numeric X-User-Id of the dedicated "product-repo" service account (provisioned once, out of
    # band, the same way the self-host admin token is provisioned — see the README's setup section).
    # Its OWN primary workspace is swapped (POST /agent/workspace/swap) to the real product repo, so a
    # dispatch under this identity with no explicit `workspaces` override mounts that repo by default.
    product_repo_user_id: str = ""


def get_settings() -> Settings:
    return Settings()
